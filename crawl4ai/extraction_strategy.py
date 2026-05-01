from __future__ import annotations

import ast
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import IntFlag, auto
from re import Pattern
from typing import Any

import numpy as np
from bs4 import BeautifulSoup
from lxml import etree, html

from .model_loader import (
    calculate_batch_size,
    get_device,
    load_HF_embedding_model,
    load_text_multilabel_classifier,
)
from .models import *  # noqa: F403
from .utils import *  # noqa: F403


# Maximum source length for a computed-field expression. Generous for
# real-world arithmetic (`price * qty * (1 - discount)`) but small enough
# to keep AST parsing/validation cheap and bound how big a literal an
# attacker can squeeze in.
_SAFE_EVAL_MAX_LEN = 500

# Maximum absolute value allowed for a numeric literal inside an
# expression. Without this an attacker can write `[0] * 10000000000` to
# OOM the worker; 10**6 is far above anything a real schema needs.
_SAFE_EVAL_MAX_LITERAL = 10**6

# `ast.Index` was deprecated in Python 3.9 (subscripts are now plain
# expressions) and removed/aliased in later versions. Reference it
# defensively so the allowlist still loads on 3.13+.
_AST_INDEX = getattr(ast, "Index", ast.AST)

_SAFE_EVAL_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp,
    ast.IfExp, ast.Compare,
    ast.Constant, ast.Attribute, ast.Subscript, ast.Name,
    ast.Load, ast.Slice, _AST_INDEX,
    # Pow (`**`) is intentionally omitted: `2 ** (10 ** 9)` is a one-line
    # CPU/memory bomb and exponentiation isn't needed for field math.
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    ast.USub, ast.UAdd,
    ast.Tuple, ast.List,
    ast.JoinedStr, ast.FormattedValue,
)


def _safe_eval_expression(expression: str, item: dict):
    """Evaluate a restricted arithmetic/attribute expression without
    allowing arbitrary code execution. Only simple names, attribute
    access, subscripts, comparisons, boolean ops, arithmetic, and string
    operations are permitted — no calls, imports, comprehensions, or
    exponentiation.

    Closes CVE-2026-26216 by replacing the unrestricted ``eval()`` that
    previously ran on user-supplied schema expressions.
    """
    if not isinstance(expression, str):
        raise ValueError("Expression must be a string")
    if len(expression) > _SAFE_EVAL_MAX_LEN:
        raise ValueError(
            f"Expression exceeds maximum length of {_SAFE_EVAL_MAX_LEN} chars"
        )

    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_EVAL_NODES):
            raise ValueError(
                f"Disallowed expression node: {type(node).__name__}"
            )
        # Cap numeric literals so `[0] * 9999999999` can't OOM the worker.
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if abs(node.value) > _SAFE_EVAL_MAX_LITERAL:
                raise ValueError(
                    f"Numeric literal exceeds maximum of {_SAFE_EVAL_MAX_LITERAL}"
                )

    code = compile(tree, "<expression>", "eval")
    return eval(code, {"__builtins__": {}}, item)  # noqa: S307


class ExtractionStrategy(ABC):
    """
    Abstract base class for all extraction strategies.
    """

    def __init__(self, input_format: str = "markdown", **kwargs):
        """
        Initialize the extraction strategy.

        Args:
            input_format: Content format to use for extraction.
                         Options: "markdown" (default), "html", "fit_markdown"
            **kwargs: Additional keyword arguments
        """
        self.input_format = input_format
        self.DEL = "<|DEL|>"
        self.name = self.__class__.__name__
        self.verbose = kwargs.get("verbose", False)

    @abstractmethod
    def extract(self, url: str, html: str, *q, **kwargs) -> list[dict[str, Any]]:
        """
        Extract meaningful blocks or chunks from the given HTML.

        :param url: The URL of the webpage.
        :param html: The HTML content of the webpage.
        :return: A list of extracted blocks or chunks.
        """
        pass

    def run(self, url: str, sections: list[str], *q, **kwargs) -> list[dict[str, Any]]:
        """
        Process sections of text in parallel by default.

        :param url: The URL of the webpage.
        :param sections: List of sections (strings) to process.
        :return: A list of processed JSON blocks.
        """
        extracted_content = []
        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(self.extract, url, section, **kwargs)
                for section in sections
            ]
            for future in as_completed(futures):
                extracted_content.extend(future.result())
        return extracted_content


class NoExtractionStrategy(ExtractionStrategy):
    """
    A strategy that does not extract any meaningful content from the HTML. It simply returns the entire HTML as a single block.
    """

    def extract(self, url: str, html: str, *q, **kwargs) -> list[dict[str, Any]]:
        """
        Extract meaningful blocks or chunks from the given HTML.
        """
        return [{"index": 0, "content": html}]

    def run(self, url: str, sections: list[str], *q, **kwargs) -> list[dict[str, Any]]:
        return [
            {"index": i, "tags": [], "content": section}
            for i, section in enumerate(sections)
        ]


#######################################################
# Strategies using clustering for text data extraction #
#######################################################


class CosineStrategy(ExtractionStrategy):
    """
    Extract meaningful blocks or chunks from the given HTML using cosine similarity.

    How it works:
    1. Pre-filter documents using embeddings and semantic_filter.
    2. Perform clustering using cosine similarity.
    3. Organize texts by their cluster labels, retaining order.
    4. Filter clusters by word count.
    5. Extract meaningful blocks or chunks from the filtered clusters.

    Attributes:
        semantic_filter (str): A keyword filter for document filtering.
        word_count_threshold (int): Minimum number of words per cluster.
        max_dist (float): The maximum cophenetic distance on the dendrogram to form clusters.
        linkage_method (str): The linkage method for hierarchical clustering.
        top_k (int): Number of top categories to extract.
        model_name (str): The name of the sentence-transformers model.
        sim_threshold (float): The similarity threshold for clustering.
    """

    def __init__(
        self,
        semantic_filter=None,
        word_count_threshold=10,
        max_dist=0.2,
        linkage_method="ward",
        top_k=3,
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        sim_threshold=0.3,
        **kwargs,
    ):
        """
        Initialize the strategy with clustering parameters.

        Args:
            semantic_filter (str): A keyword filter for document filtering.
            word_count_threshold (int): Minimum number of words per cluster.
            max_dist (float): The maximum cophenetic distance on the dendrogram to form clusters.
            linkage_method (str): The linkage method for hierarchical clustering.
            top_k (int): Number of top categories to extract.
        """
        super().__init__(**kwargs)

        import numpy as np

        self.semantic_filter = semantic_filter
        self.word_count_threshold = word_count_threshold
        self.max_dist = max_dist
        self.linkage_method = linkage_method
        self.top_k = top_k
        self.sim_threshold = sim_threshold
        self.timer = time.time()
        self.verbose = kwargs.get("verbose", False)

        self.buffer_embeddings = np.array([])
        self.get_embedding_method = "direct"

        self.device = get_device()
        # import torch
        # self.device = torch.device('cpu')

        self.default_batch_size = calculate_batch_size(self.device)

        if self.verbose:
            print(f"[LOG] Loading Extraction Model for {self.device.type} device.")

        # if False and self.device.type == "cpu":
        #     self.model = load_onnx_all_MiniLM_l6_v2()
        #     self.tokenizer = self.model.tokenizer
        #     self.get_embedding_method = "direct"
        # else:

        self.tokenizer, self.model = load_HF_embedding_model(model_name)
        self.model.to(self.device)
        self.model.eval()

        self.get_embedding_method = "batch"

        self.buffer_embeddings = np.array([])

        # if model_name == "bert-base-uncased":
        #     self.tokenizer, self.model = load_bert_base_uncased()
        #     self.model.eval()  # Ensure the model is in evaluation mode
        #     self.get_embedding_method = "batch"
        # elif model_name == "BAAI/bge-small-en-v1.5":
        #     self.tokenizer, self.model = load_bge_small_en_v1_5()
        #     self.model.eval()  # Ensure the model is in evaluation mode
        #     self.get_embedding_method = "batch"
        # elif model_name == "sentence-transformers/all-MiniLM-L6-v2":
        #     self.model = load_onnx_all_MiniLM_l6_v2()
        #     self.tokenizer = self.model.tokenizer
        #     self.get_embedding_method = "direct"

        if self.verbose:
            print(f"[LOG] Loading Multilabel Classifier for {self.device.type} device.")

        self.nlp, _ = load_text_multilabel_classifier()
        # self.default_batch_size = 16 if self.device.type == 'cpu' else 64

        if self.verbose:
            print(
                f"[LOG] Model loaded {model_name}, models/reuters, took "
                + str(time.time() - self.timer)
                + " seconds"
            )

    def filter_documents_embeddings(
        self, documents: list[str], semantic_filter: str, at_least_k: int = 20
    ) -> list[str]:
        """
        Filter and sort documents based on the cosine similarity of their embeddings with the semantic_filter embedding.

        Args:
            documents (List[str]): A list of document texts.
            semantic_filter (str): A keyword filter for document filtering.
            at_least_k (int): The minimum number of documents to return.

        Returns:
            List[str]: A list of filtered and sorted document texts.
        """

        if not semantic_filter:
            return documents

        if len(documents) < at_least_k:
            at_least_k = len(documents) // 2

        from sklearn.metrics.pairwise import cosine_similarity

        # Compute embedding for the keyword filter
        query_embedding = self.get_embeddings([semantic_filter])[0]

        # Compute embeddings for the documents
        document_embeddings = self.get_embeddings(documents)

        # Calculate cosine similarity between the query embedding and document embeddings
        similarities = cosine_similarity(
            [query_embedding], document_embeddings
        ).flatten()

        # Filter documents based on the similarity threshold
        filtered_docs = [
            (doc, sim)
            for doc, sim in zip(documents, similarities)
            if sim >= self.sim_threshold
        ]

        # If the number of filtered documents is less than at_least_k, sort remaining documents by similarity
        if len(filtered_docs) < at_least_k:
            remaining_docs = [
                (doc, sim)
                for doc, sim in zip(documents, similarities)
                if sim < self.sim_threshold
            ]
            remaining_docs.sort(key=lambda x: x[1], reverse=True)
            filtered_docs.extend(remaining_docs[: at_least_k - len(filtered_docs)])

        # Extract the document texts from the tuples
        filtered_docs = [doc for doc, _ in filtered_docs]

        return filtered_docs[:at_least_k]

    def get_embeddings(
        self, sentences: list[str], batch_size=None, bypass_buffer=False
    ):
        """
        Get BERT embeddings for a list of sentences.

        Args:
            sentences (List[str]): A list of text chunks (sentences).

        Returns:
            NumPy array of embeddings.
        """
        # if self.buffer_embeddings.any() and not bypass_buffer:
        #     return self.buffer_embeddings

        if self.device.type in ["cpu", "gpu", "cuda", "mps"]:
            import torch

            # Tokenize sentences and convert to tensor
            if batch_size is None:
                batch_size = self.default_batch_size

            all_embeddings = []
            for i in range(0, len(sentences), batch_size):
                batch_sentences = sentences[i : i + batch_size]
                encoded_input = self.tokenizer(
                    batch_sentences, padding=True, truncation=True, return_tensors="pt"
                )
                encoded_input = {
                    key: tensor.to(self.device) for key, tensor in encoded_input.items()
                }

                # Ensure no gradients are calculated
                with torch.no_grad():
                    model_output = self.model(**encoded_input)

                # Get embeddings from the last hidden state (mean pooling)
                embeddings = model_output.last_hidden_state.mean(dim=1).cpu().numpy()
                all_embeddings.append(embeddings)

            self.buffer_embeddings = np.vstack(all_embeddings)
        elif self.device.type == "cpu":
            # self.buffer_embeddings = self.model(sentences)
            if batch_size is None:
                batch_size = self.default_batch_size

            all_embeddings = []
            for i in range(0, len(sentences), batch_size):
                batch_sentences = sentences[i : i + batch_size]
                embeddings = self.model(batch_sentences)
                all_embeddings.append(embeddings)

            self.buffer_embeddings = np.vstack(all_embeddings)
        return self.buffer_embeddings

    def hierarchical_clustering(self, sentences: list[str], embeddings=None):
        """
        Perform hierarchical clustering on sentences and return cluster labels.

        Args:
            sentences (List[str]): A list of text chunks (sentences).

        Returns:
            NumPy array of cluster labels.
        """
        # Get embeddings
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import pdist

        self.timer = time.time()
        embeddings = self.get_embeddings(sentences, bypass_buffer=True)
        # print(f"[LOG] 🚀 Embeddings computed in {time.time() - self.timer:.2f} seconds")
        # Compute pairwise cosine distances
        distance_matrix = pdist(embeddings, "cosine")
        # Perform agglomerative clustering respecting order
        linked = linkage(distance_matrix, method=self.linkage_method)
        # Form flat clusters
        labels = fcluster(linked, self.max_dist, criterion="distance")
        return labels

    def filter_clusters_by_word_count(
        self, clusters: dict[int, list[str]]
    ) -> dict[int, list[str]]:
        """
        Filter clusters to remove those with a word count below the threshold.

        Args:
            clusters (Dict[int, List[str]]): Dictionary of clusters.

        Returns:
            Dict[int, List[str]]: Filtered dictionary of clusters.
        """
        filtered_clusters = {}
        for cluster_id, texts in clusters.items():
            # Concatenate texts for analysis
            full_text = " ".join(texts)
            # Count words
            word_count = len(full_text.split())

            # Keep clusters with word count above the threshold
            if word_count >= self.word_count_threshold:
                filtered_clusters[cluster_id] = texts

        return filtered_clusters

    def extract(self, url: str, html: str, *q, **kwargs) -> list[dict[str, Any]]:
        """
        Extract clusters from HTML content using hierarchical clustering.

        Args:
            url (str): The URL of the webpage.
            html (str): The HTML content of the webpage.

        Returns:
            List[Dict[str, Any]]: A list of processed JSON blocks.
        """
        # Assume `html` is a list of text chunks for this strategy
        t = time.time()
        text_chunks = html.split(self.DEL)  # Split by lines or paragraphs as needed

        # Pre-filter documents using embeddings and semantic_filter
        text_chunks = self.filter_documents_embeddings(
            text_chunks, self.semantic_filter
        )

        if not text_chunks:
            return []

        # Perform clustering
        labels = self.hierarchical_clustering(text_chunks)
        # print(f"[LOG] 🚀 Clustering done in {time.time() - t:.2f} seconds")

        # Organize texts by their cluster labels, retaining order
        t = time.time()
        clusters = {}
        for index, label in enumerate(labels):
            clusters.setdefault(label, []).append(text_chunks[index])

        # Filter clusters by word count
        filtered_clusters = self.filter_clusters_by_word_count(clusters)

        # Convert filtered clusters to a sorted list of dictionaries
        cluster_list = [
            {"index": int(idx), "tags": [], "content": " ".join(filtered_clusters[idx])}
            for idx in sorted(filtered_clusters)
        ]

        if self.verbose:
            print(f"[LOG] 🚀 Assign tags using {self.device}")

        if self.device.type in ["gpu", "cuda", "mps", "cpu"]:
            labels = self.nlp([cluster["content"] for cluster in cluster_list])

            for cluster, label in zip(cluster_list, labels):
                cluster["tags"] = label
        # elif self.device.type == "cpu":
        #     # Process the text with the loaded model
        #     texts = [cluster['content'] for cluster in cluster_list]
        #     # Batch process texts
        #     docs = self.nlp.pipe(texts, disable=["tagger", "parser", "ner", "lemmatizer"])

        #     for doc, cluster in zip(docs, cluster_list):
        #         tok_k = self.top_k
        #         top_categories = sorted(doc.cats.items(), key=lambda x: x[1], reverse=True)[:tok_k]
        #         cluster['tags'] = [cat for cat, _ in top_categories]

        # for cluster in  cluster_list:
        #     doc = self.nlp(cluster['content'])
        #     tok_k = self.top_k
        #     top_categories = sorted(doc.cats.items(), key=lambda x: x[1], reverse=True)[:tok_k]
        #     cluster['tags'] = [cat for cat, _ in top_categories]

        if self.verbose:
            print(f"[LOG] 🚀 Categorization done in {time.time() - t:.2f} seconds")

        return cluster_list

    def run(self, url: str, sections: list[str], *q, **kwargs) -> list[dict[str, Any]]:
        """
        Process sections using hierarchical clustering.

        Args:
            url (str): The URL of the webpage.
            sections (List[str]): List of sections (strings) to process.

        Returns:
        """
        # This strategy processes all sections together

        return self.extract(url, self.DEL.join(sections), **kwargs)


#######################################################
# New extraction strategies for JSON-based extraction #
#######################################################
class JsonElementExtractionStrategy(ExtractionStrategy):
    """
    Abstract base class for extracting structured JSON from HTML content.

    How it works:
    1. Parses HTML content using the `_parse_html` method.
    2. Uses a schema to define base selectors, fields, and transformations.
    3. Extracts data hierarchically, supporting nested fields and lists.
    4. Handles computed fields with expressions or functions.

    Attributes:
        DEL (str): Delimiter used to combine HTML sections. Defaults to '\n'.
        schema (Dict[str, Any]): The schema defining the extraction rules.
        verbose (bool): Enables verbose logging for debugging purposes.

    Methods:
        extract(url, html_content, *q, **kwargs): Extracts structured data from HTML content.
        _extract_item(element, fields): Extracts fields from a single element.
        _extract_single_field(element, field): Extracts a single field based on its type.
        _apply_transform(value, transform): Applies a transformation to a value.
        _compute_field(item, field): Computes a field value using an expression or function.
        run(url, sections, *q, **kwargs): Combines HTML sections and runs the extraction strategy.

    Abstract Methods:
        _parse_html(html_content): Parses raw HTML into a structured format (e.g., BeautifulSoup or lxml).
        _get_base_elements(parsed_html, selector): Retrieves base elements using a selector.
        _get_elements(element, selector): Retrieves child elements using a selector.
        _get_element_text(element): Extracts text content from an element.
        _get_element_html(element): Extracts raw HTML from an element.
        _get_element_attribute(element, attribute): Extracts an attribute's value from an element.
    """

    DEL = "\n"

    def __init__(self, schema: dict[str, Any], **kwargs):
        """
        Initialize the JSON element extraction strategy with a schema.

        Args:
            schema (Dict[str, Any]): The schema defining the extraction rules.
        """
        super().__init__(**kwargs)
        self.schema = schema
        self.verbose = kwargs.get("verbose", False)

    def extract(
        self, url: str, html_content: str, *q, **kwargs
    ) -> list[dict[str, Any]]:
        """
        Extract structured data from HTML content.

        How it works:
        1. Parses the HTML content using the `_parse_html` method.
        2. Identifies base elements using the schema's base selector.
        3. Extracts fields from each base element using `_extract_item`.

        Args:
            url (str): The URL of the page being processed.
            html_content (str): The raw HTML content to parse and extract.
            *q: Additional positional arguments.
            **kwargs: Additional keyword arguments for custom extraction.

        Returns:
            List[Dict[str, Any]]: A list of extracted items, each represented as a dictionary.
        """

        parsed_html = self._parse_html(html_content)
        base_elements = self._get_base_elements(
            parsed_html, self.schema["baseSelector"]
        )

        results = []
        for element in base_elements:
            # Extract base element attributes
            item = {}
            if "baseFields" in self.schema:
                for field in self.schema["baseFields"]:
                    value = self._extract_single_field(element, field)
                    if value is not None:
                        item[field["name"]] = value

            # Extract child fields
            field_data = self._extract_item(element, self.schema["fields"])
            item.update(field_data)

            if item:
                results.append(item)

        return results

    @abstractmethod
    def _parse_html(self, html_content: str):
        """Parse HTML content into appropriate format"""
        pass

    @abstractmethod
    def _get_base_elements(self, parsed_html, selector: str):
        """Get all base elements using the selector"""
        pass

    @abstractmethod
    def _get_elements(self, element, selector: str):
        """Get child elements using the selector"""
        pass

    def _extract_field(self, element, field):
        try:
            if field["type"] == "nested":
                nested_elements = self._get_elements(element, field["selector"])
                nested_element = nested_elements[0] if nested_elements else None
                return (
                    self._extract_item(nested_element, field["fields"])
                    if nested_element
                    else {}
                )

            if field["type"] == "list":
                elements = self._get_elements(element, field["selector"])
                return [self._extract_list_item(el, field["fields"]) for el in elements]

            if field["type"] == "nested_list":
                elements = self._get_elements(element, field["selector"])
                return [self._extract_item(el, field["fields"]) for el in elements]

            return self._extract_single_field(element, field)
        except Exception as e:
            if self.verbose:
                print(f"Error extracting field {field['name']}: {str(e)}")
            return field.get("default")

    def _extract_single_field(self, element, field):
        """
        Extract a single field based on its type.

        How it works:
        1. Selects the target element using the field's selector.
        2. Extracts the field value based on its type (e.g., text, attribute, regex).
        3. Applies transformations if defined in the schema.

        Args:
            element: The base element to extract the field from.
            field (Dict[str, Any]): The field definition in the schema.

        Returns:
            Any: The extracted field value.
        """

        if "selector" in field:
            selected = self._get_elements(element, field["selector"])
            if not selected:
                return field.get("default")
            selected = selected[0]
        else:
            selected = element

        value = None
        if field["type"] == "text":
            value = self._get_element_text(selected)
        elif field["type"] == "attribute":
            value = self._get_element_attribute(selected, field["attribute"])
        elif field["type"] == "html":
            value = self._get_element_html(selected)
        elif field["type"] == "regex":
            text = self._get_element_text(selected)
            match = re.search(field["pattern"], text)
            value = match.group(1) if match else None

        if "transform" in field:
            value = self._apply_transform(value, field["transform"])

        return value if value is not None else field.get("default")

    def _extract_list_item(self, element, fields):
        item = {}
        for field in fields:
            value = self._extract_single_field(element, field)
            if value is not None:
                item[field["name"]] = value
        return item

    def _extract_item(self, element, fields):
        """
        Extracts fields from a given element.

        How it works:
        1. Iterates through the fields defined in the schema.
        2. Handles computed, single, and nested field types.
        3. Updates the item dictionary with extracted field values.

        Args:
            element: The base element to extract fields from.
            fields (List[Dict[str, Any]]): The list of fields to extract.

        Returns:
            Dict[str, Any]: A dictionary representing the extracted item.
        """

        item = {}
        for field in fields:
            if field["type"] == "computed":
                value = self._compute_field(item, field)
            else:
                value = self._extract_field(element, field)
            if value is not None:
                item[field["name"]] = value
        return item

    def _apply_transform(self, value, transform):
        """
        Apply a transformation to a value.

        How it works:
        1. Checks the transformation type (e.g., `lowercase`, `strip`).
        2. Applies the transformation to the value.
        3. Returns the transformed value.

        Args:
            value (str): The value to transform.
            transform (str): The type of transformation to apply.

        Returns:
            str: The transformed value.
        """

        if transform == "lowercase":
            return value.lower()
        if transform == "uppercase":
            return value.upper()
        if transform == "strip":
            return value.strip()
        return value

    def _compute_field(self, item, field):
        try:
            if "expression" in field:
                return _safe_eval_expression(field["expression"], item)
            if "function" in field:
                return field["function"](item)
        except Exception as e:
            if self.verbose:
                print(f"Error computing field {field['name']}: {str(e)}")
            return field.get("default")

    def run(self, url: str, sections: list[str], *q, **kwargs) -> list[dict[str, Any]]:
        """
        Run the extraction strategy on a combined HTML content.

        How it works:
        1. Combines multiple HTML sections using the `DEL` delimiter.
        2. Calls the `extract` method with the combined HTML.

        Args:
            url (str): The URL of the page being processed.
            sections (List[str]): A list of HTML sections.
            *q: Additional positional arguments.
            **kwargs: Additional keyword arguments for custom extraction.

        Returns:
            List[Dict[str, Any]]: A list of extracted items.
        """

        combined_html = self.DEL.join(sections)
        return self.extract(url, combined_html, **kwargs)

    @abstractmethod
    def _get_element_text(self, element) -> str:
        """Get text content from element"""
        pass

    @abstractmethod
    def _get_element_html(self, element) -> str:
        """Get HTML content from element"""
        pass

    @abstractmethod
    def _get_element_attribute(self, element, attribute: str):
        """Get attribute value from element"""
        pass

class JsonCssExtractionStrategy(JsonElementExtractionStrategy):
    """
    Concrete implementation of `JsonElementExtractionStrategy` using CSS selectors.

    How it works:
    1. Parses HTML content with BeautifulSoup.
    2. Selects elements using CSS selectors defined in the schema.
    3. Extracts field data and applies transformations as defined.

    Attributes:
        schema (Dict[str, Any]): The schema defining the extraction rules.
        verbose (bool): Enables verbose logging for debugging purposes.

    Methods:
        _parse_html(html_content): Parses HTML content into a BeautifulSoup object.
        _get_base_elements(parsed_html, selector): Selects base elements using a CSS selector.
        _get_elements(element, selector): Selects child elements using a CSS selector.
        _get_element_text(element): Extracts text content from a BeautifulSoup element.
        _get_element_html(element): Extracts the raw HTML content of a BeautifulSoup element.
        _get_element_attribute(element, attribute): Retrieves an attribute value from a BeautifulSoup element.
    """

    def __init__(self, schema: dict[str, Any], **kwargs):
        kwargs["input_format"] = "html"  # Force HTML input
        super().__init__(schema, **kwargs)

    def _parse_html(self, html_content: str):
        # return BeautifulSoup(html_content, "html.parser")
        return BeautifulSoup(html_content, "lxml")

    def _get_base_elements(self, parsed_html, selector: str):
        return parsed_html.select(selector)

    def _get_elements(self, element, selector: str):
        # Return all matching elements using select() instead of select_one()
        # This ensures that we get all elements that match the selector, not just the first one
        return element.select(selector)

    def _get_element_text(self, element) -> str:
        return element.get_text(strip=True)

    def _get_element_html(self, element) -> str:
        return str(element)

    def _get_element_attribute(self, element, attribute: str):
        return element.get(attribute)

class JsonLxmlExtractionStrategy(JsonElementExtractionStrategy):
    def __init__(self, schema: dict[str, Any], **kwargs):
        kwargs["input_format"] = "html"
        super().__init__(schema, **kwargs)
        self._selector_cache = {}
        self._xpath_cache = {}
        self._result_cache = {}
        
        # Control selector optimization strategy
        self.use_caching = kwargs.get("use_caching", True)
        self.optimize_common_patterns = kwargs.get("optimize_common_patterns", True)
        
        # Load lxml dependencies once
        from lxml import etree, html
        from lxml.cssselect import CSSSelector
        self.etree = etree
        self.html_parser = html
        self.CSSSelector = CSSSelector
    
    def _parse_html(self, html_content: str):
        """Parse HTML content with error recovery"""
        try:
            parser = self.etree.HTMLParser(recover=True, remove_blank_text=True)
            return self.etree.fromstring(html_content, parser)
        except Exception as e:
            if self.verbose:
                print(f"Error parsing HTML, falling back to alternative method: {e}")
            try:
                return self.html_parser.fromstring(html_content)
            except Exception as e2:
                if self.verbose:
                    print(f"Critical error parsing HTML: {e2}")
                # Create minimal document as fallback
                return self.etree.Element("html")
    
    def _optimize_selector(self, selector_str):
        """Optimize common selector patterns for better performance"""
        if not self.optimize_common_patterns:
            return selector_str
            
        # Handle td:nth-child(N) pattern which is very common in table scraping
        import re
        if re.search(r'td:nth-child\(\d+\)', selector_str):
            return selector_str  # Already handled specially in _apply_selector
            
        # Split complex selectors into parts for optimization
        parts = selector_str.split()
        if len(parts) <= 1:
            return selector_str
            
        # For very long selectors, consider using just the last specific part
        if len(parts) > 3 and any(p.startswith('.') or p.startswith('#') for p in parts):
            specific_parts = [p for p in parts if p.startswith('.') or p.startswith('#')]
            if specific_parts:
                return specific_parts[-1]  # Use most specific class/id selector
                
        return selector_str
    
    def _create_selector_function(self, selector_str):
        """Create a selector function that handles all edge cases"""
        original_selector = selector_str
        
        # Try to optimize the selector if appropriate
        if self.optimize_common_patterns:
            selector_str = self._optimize_selector(selector_str)
        
        try:
            # Attempt to compile the CSS selector
            compiled = self.CSSSelector(selector_str)
            xpath = compiled.path
            
            # Store XPath for later use
            self._xpath_cache[selector_str] = xpath
            
            # Create the wrapper function that implements the selection strategy
            def selector_func(element, context_sensitive=True):
                cache_key = None
                
                # Use result caching if enabled
                if self.use_caching:
                    # Create a cache key based on element and selector
                    element_id = element.get('id', '') or str(hash(element))
                    cache_key = f"{element_id}::{selector_str}"
                    
                    if cache_key in self._result_cache:
                        return self._result_cache[cache_key]
                
                results = []
                try:
                    # Strategy 1: Direct CSS selector application (fastest)
                    results = compiled(element)
                    
                    # If that fails and we need context sensitivity
                    if not results and context_sensitive:
                        # Strategy 2: Try XPath with context adjustment
                        context_xpath = self._make_context_sensitive_xpath(xpath, element)
                        if context_xpath:
                            results = element.xpath(context_xpath)
                        
                        # Strategy 3: Handle special case - nth-child
                        if not results and 'nth-child' in original_selector:
                            results = self._handle_nth_child_selector(element, original_selector)
                        
                        # Strategy 4: Direct descendant search for class/ID selectors
                        if not results:
                            results = self._fallback_class_id_search(element, original_selector)
                            
                        # Strategy 5: Last resort - tag name search for the final part
                        if not results:
                            parts = original_selector.split()
                            if parts:
                                last_part = parts[-1]
                                # Extract tag name from the selector
                                tag_match = re.match(r'^(\w+)', last_part)
                                if tag_match:
                                    tag_name = tag_match.group(1)
                                    results = element.xpath(f".//{tag_name}")
                    
                    # Cache results if caching is enabled
                    if self.use_caching and cache_key:
                        self._result_cache[cache_key] = results
                        
                except Exception as e:
                    if self.verbose:
                        print(f"Error applying selector '{selector_str}': {e}")
                
                return results
                
            return selector_func
            
        except Exception as e:
            if self.verbose:
                print(f"Error compiling selector '{selector_str}': {e}")
            
            # Fallback function for invalid selectors
            return lambda element, context_sensitive=True: []
    
    def _make_context_sensitive_xpath(self, xpath, element):
        """Convert absolute XPath to context-sensitive XPath"""
        try:
            # If starts with descendant-or-self, it's already context-sensitive
            if xpath.startswith('descendant-or-self::'):
                return xpath
                
            # Remove leading slash if present
            if xpath.startswith('/'):
                context_xpath = f".{xpath}"
            else:
                context_xpath = f".//{xpath}"
                
            # Validate the XPath by trying it
            try:
                element.xpath(context_xpath)
                return context_xpath
            except:
                # If that fails, try a simpler descendant search
                return f".//{xpath.split('/')[-1]}"
        except:
            return None
    
    def _handle_nth_child_selector(self, element, selector_str):
        """Special handling for nth-child selectors in tables"""
        import re
        results = []
        
        try:
            # Extract the column number from td:nth-child(N)
            match = re.search(r'td:nth-child\((\d+)\)', selector_str)
            if match:
                col_num = match.group(1)
                
                # Check if there's content after the nth-child part
                remaining_selector = selector_str.split(f"td:nth-child({col_num})", 1)[-1].strip()
                
                if remaining_selector:
                    # If there's a specific element we're looking for after the column
                    # Extract any tag names from the remaining selector
                    tag_match = re.search(r'(\w+)', remaining_selector)
                    tag_name = tag_match.group(1) if tag_match else '*'
                    results = element.xpath(f".//td[{col_num}]//{tag_name}")
                else:
                    # Just get the column cell
                    results = element.xpath(f".//td[{col_num}]")
        except Exception as e:
            if self.verbose:
                print(f"Error handling nth-child selector: {e}")
                
        return results
    
    def _fallback_class_id_search(self, element, selector_str):
        """Fallback to search by class or ID"""
        results = []
        
        try:
            # Extract class selectors (.classname)
            import re
            class_matches = re.findall(r'\.([a-zA-Z0-9_-]+)', selector_str)
            
            # Extract ID selectors (#idname)
            id_matches = re.findall(r'#([a-zA-Z0-9_-]+)', selector_str)
            
            # Try each class
            for class_name in class_matches:
                class_results = element.xpath(f".//*[contains(@class, '{class_name}')]")
                results.extend(class_results)
                
            # Try each ID (usually more specific)
            for id_name in id_matches:
                id_results = element.xpath(f".//*[@id='{id_name}']")
                results.extend(id_results)
        except Exception as e:
            if self.verbose:
                print(f"Error in fallback class/id search: {e}")
                
        return results
    
    def _get_selector(self, selector_str):
        """Get or create a selector function with caching"""
        if selector_str not in self._selector_cache:
            self._selector_cache[selector_str] = self._create_selector_function(selector_str)
        return self._selector_cache[selector_str]
    
    def _get_base_elements(self, parsed_html, selector: str):
        """Get all base elements using the selector"""
        selector_func = self._get_selector(selector)
        # For base elements, we don't need context sensitivity
        return selector_func(parsed_html, context_sensitive=False)
    
    def _get_elements(self, element, selector: str):
        """Get child elements using the selector with context sensitivity"""
        selector_func = self._get_selector(selector)
        return selector_func(element, context_sensitive=True)
    
    def _get_element_text(self, element) -> str:
        """Extract normalized text from element"""
        try:
            # Get all text nodes and normalize
            text = " ".join(t.strip() for t in element.xpath(".//text()") if t.strip())
            return text
        except Exception as e:
            if self.verbose:
                print(f"Error extracting text: {e}")
            # Fallback
            try:
                return element.text_content().strip()
            except:
                return ""
    
    def _get_element_html(self, element) -> str:
        """Get HTML string representation of element"""
        try:
            return self.etree.tostring(element, encoding='unicode', method='html')
        except Exception as e:
            if self.verbose:
                print(f"Error serializing HTML: {e}")
            return ""
    
    def _get_element_attribute(self, element, attribute: str):
        """Get attribute value safely"""
        try:
            return element.get(attribute)
        except Exception as e:
            if self.verbose:
                print(f"Error getting attribute '{attribute}': {e}")
            return None
            
    def _clear_caches(self):
        """Clear caches to free memory"""
        if self.use_caching:
            self._result_cache.clear()

class JsonLxmlExtractionStrategy_naive(JsonElementExtractionStrategy):
    def __init__(self, schema: dict[str, Any], **kwargs):
        kwargs["input_format"] = "html"  # Force HTML input
        super().__init__(schema, **kwargs)
        self._selector_cache = {}
    
    def _parse_html(self, html_content: str):
        from lxml import etree
        parser = etree.HTMLParser(recover=True)
        return etree.fromstring(html_content, parser)
    
    def _get_selector(self, selector_str):
        """Get a selector function that works within the context of an element"""
        if selector_str not in self._selector_cache:
            from lxml.cssselect import CSSSelector
            try:
                # Store both the compiled selector and its xpath translation
                compiled = CSSSelector(selector_str)
                
                # Create a function that will apply this selector appropriately
                def select_func(element):
                    try:
                        # First attempt: direct CSS selector application
                        results = compiled(element)
                        if results:
                            return results
                        
                        # Second attempt: contextual XPath selection
                        # Convert the root-based XPath to a context-based XPath
                        xpath = compiled.path
                        
                        # If the XPath already starts with descendant-or-self, handle it specially
                        if xpath.startswith('descendant-or-self::'):
                            context_xpath = xpath
                        else:
                            # For normal XPath expressions, make them relative to current context
                            context_xpath = f"./{xpath.lstrip('/')}"
                        
                        results = element.xpath(context_xpath)
                        if results:
                            return results
                        
                        # Final fallback: simple descendant search for common patterns
                        if 'nth-child' in selector_str:
                            # Handle td:nth-child(N) pattern
                            import re
                            match = re.search(r'td:nth-child\((\d+)\)', selector_str)
                            if match:
                                col_num = match.group(1)
                                sub_selector = selector_str.split(')', 1)[-1].strip()
                                if sub_selector:
                                    return element.xpath(f".//td[{col_num}]//{sub_selector}")
                                return element.xpath(f".//td[{col_num}]")
                        
                        # Last resort: try each part of the selector separately
                        parts = selector_str.split()
                        if len(parts) > 1 and parts[-1]:
                            return element.xpath(f".//{parts[-1]}")
                            
                        return []
                    except Exception as e:
                        if self.verbose:
                            print(f"Error applying selector '{selector_str}': {e}")
                        return []
                
                self._selector_cache[selector_str] = select_func
            except Exception as e:
                if self.verbose:
                    print(f"Error compiling selector '{selector_str}': {e}")
                
                # Fallback function for invalid selectors
                def fallback_func(element):
                    return []
                
                self._selector_cache[selector_str] = fallback_func
                
        return self._selector_cache[selector_str]
    
    def _get_base_elements(self, parsed_html, selector: str):
        selector_func = self._get_selector(selector)
        return selector_func(parsed_html)
    
    def _get_elements(self, element, selector: str):
        selector_func = self._get_selector(selector)
        return selector_func(element)
    
    def _get_element_text(self, element) -> str:
        return "".join(element.xpath(".//text()")).strip()
    
    def _get_element_html(self, element) -> str:
        from lxml import etree
        return etree.tostring(element, encoding='unicode')
    
    def _get_element_attribute(self, element, attribute: str):
        return element.get(attribute)    

class JsonXPathExtractionStrategy(JsonElementExtractionStrategy):
    """
    Concrete implementation of `JsonElementExtractionStrategy` using XPath selectors.

    How it works:
    1. Parses HTML content into an lxml tree.
    2. Selects elements using XPath expressions.
    3. Converts CSS selectors to XPath when needed.

    Attributes:
        schema (Dict[str, Any]): The schema defining the extraction rules.
        verbose (bool): Enables verbose logging for debugging purposes.

    Methods:
        _parse_html(html_content): Parses HTML content into an lxml tree.
        _get_base_elements(parsed_html, selector): Selects base elements using an XPath selector.
        _css_to_xpath(css_selector): Converts a CSS selector to an XPath expression.
        _get_elements(element, selector): Selects child elements using an XPath selector.
        _get_element_text(element): Extracts text content from an lxml element.
        _get_element_html(element): Extracts the raw HTML content of an lxml element.
        _get_element_attribute(element, attribute): Retrieves an attribute value from an lxml element.
    """

    def __init__(self, schema: dict[str, Any], **kwargs):
        kwargs["input_format"] = "html"  # Force HTML input
        super().__init__(schema, **kwargs)

    def _parse_html(self, html_content: str):
        return html.fromstring(html_content)

    def _get_base_elements(self, parsed_html, selector: str):
        return parsed_html.xpath(selector)

    def _css_to_xpath(self, css_selector: str) -> str:
        """Convert CSS selector to XPath if needed"""
        if "/" in css_selector:  # Already an XPath
            return css_selector
        return self._basic_css_to_xpath(css_selector)

    def _basic_css_to_xpath(self, css_selector: str) -> str:
        """Basic CSS to XPath conversion for common cases"""
        if " > " in css_selector:
            parts = css_selector.split(" > ")
            return "//" + "/".join(parts)
        if " " in css_selector:
            parts = css_selector.split(" ")
            return "//" + "//".join(parts)
        return "//" + css_selector

    def _get_elements(self, element, selector: str):
        xpath = self._css_to_xpath(selector)
        if not xpath.startswith("."):
            xpath = "." + xpath
        return element.xpath(xpath)

    def _get_element_text(self, element) -> str:
        return "".join(element.xpath(".//text()")).strip()

    def _get_element_html(self, element) -> str:
        return etree.tostring(element, encoding="unicode")

    def _get_element_attribute(self, element, attribute: str):
        return element.get(attribute)

"""
RegexExtractionStrategy
Fast, zero-LLM extraction of common entities via regular expressions.
"""

_CTRL = {c: rf"\x{ord(c):02x}" for c in map(chr, range(32)) if c not in "\t\n\r"}

_WB_FIX = re.compile(r"\x08")               # stray back-space   →   word-boundary
_NEEDS_ESCAPE = re.compile(r"(?<!\\)\\(?![\\u])")   # lone backslash

def _sanitize_schema(schema: dict[str, str]) -> dict[str, str]:
    """Fix common JSON-escape goofs coming from LLMs or manual edits."""
    safe = {}
    for label, pat in schema.items():
        # 1️⃣ replace accidental control chars (inc. the infamous back-space)
        pat = _WB_FIX.sub(r"\\b", pat).translate(_CTRL)

        # 2️⃣ double any single backslash that JSON kept single
        pat = _NEEDS_ESCAPE.sub(r"\\\\", pat)

        # 3️⃣ quick sanity compile
        try:
            re.compile(pat)
        except re.error as e:
            raise ValueError(f"Regex for '{label}' won’t compile after fix: {e}") from None

        safe[label] = pat
    return safe


class RegexExtractionStrategy(ExtractionStrategy):
    """
    A lean strategy that finds e-mails, phones, URLs, dates, money, etc.,
    using nothing but pre-compiled regular expressions.

    Extraction returns::

        {
            "url":   "<page-url>",
            "label": "<pattern-label>",
            "value": "<matched-string>",
            "span":  [start, end]
        }

    Only `generate_schema()` touches an LLM, extraction itself is pure Python.
    """

    # -------------------------------------------------------------- #
    # Built-in patterns exposed as IntFlag so callers can bit-OR them
    # -------------------------------------------------------------- #
    class _B(IntFlag):
        EMAIL           = auto()
        PHONE_INTL      = auto()
        PHONE_US        = auto()
        URL             = auto()
        IPV4            = auto()
        IPV6            = auto()
        UUID            = auto()
        CURRENCY        = auto()
        PERCENTAGE      = auto()
        NUMBER          = auto()
        DATE_ISO        = auto()
        DATE_US         = auto()
        TIME_24H        = auto()
        POSTAL_US       = auto()
        POSTAL_UK       = auto()
        HTML_COLOR_HEX  = auto()
        TWITTER_HANDLE  = auto()
        HASHTAG         = auto()
        MAC_ADDR        = auto()
        IBAN            = auto()
        CREDIT_CARD     = auto()
        NOTHING         = auto()
        ALL             = (
            EMAIL | PHONE_INTL | PHONE_US | URL | IPV4 | IPV6 | UUID
            | CURRENCY | PERCENTAGE | NUMBER | DATE_ISO | DATE_US | TIME_24H
            | POSTAL_US | POSTAL_UK | HTML_COLOR_HEX | TWITTER_HANDLE
            | HASHTAG | MAC_ADDR | IBAN | CREDIT_CARD
        )

    # user-friendly aliases  (RegexExtractionStrategy.Email, .IPv4, …)
    Email          = _B.EMAIL
    PhoneIntl      = _B.PHONE_INTL
    PhoneUS        = _B.PHONE_US
    Url            = _B.URL
    IPv4           = _B.IPV4
    IPv6           = _B.IPV6
    Uuid           = _B.UUID
    Currency       = _B.CURRENCY
    Percentage     = _B.PERCENTAGE
    Number         = _B.NUMBER
    DateIso        = _B.DATE_ISO
    DateUS         = _B.DATE_US
    Time24h        = _B.TIME_24H
    PostalUS       = _B.POSTAL_US
    PostalUK       = _B.POSTAL_UK
    HexColor       = _B.HTML_COLOR_HEX
    TwitterHandle  = _B.TWITTER_HANDLE
    Hashtag        = _B.HASHTAG
    MacAddr        = _B.MAC_ADDR
    Iban           = _B.IBAN
    CreditCard     = _B.CREDIT_CARD
    All            = _B.ALL
    Nothing        = _B(0)  # no patterns

    # ------------------------------------------------------------------ #
    # Built-in pattern catalog
    # ------------------------------------------------------------------ #
    DEFAULT_PATTERNS: dict[str, str] = {
        # Communication
        "email":           r"[\w.+-]+@[\w-]+\.[\w.-]+",
        "phone_intl":      r"\+?\d[\d .()-]{7,}\d",
        "phone_us":        r"\(?\d{3}\)?[ -. ]?\d{3}[ -. ]?\d{4}",
        # Web
        "url":             r"https?://[^\s\"'<>]+",
        "ipv4":            r"(?:\d{1,3}\.){3}\d{1,3}",
        "ipv6":            r"[A-F0-9]{1,4}(?::[A-F0-9]{1,4}){7}",
        # IDs
        "uuid":            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        # Money / numbers
        "currency":        r"(?:USD|EUR|RM|\$|€|£)\s?\d+(?:[.,]\d{2})?",
        "percentage":      r"\d+(?:\.\d+)?%",
        "number":          r"\b\d{1,3}(?:[,.\s]\d{3})*(?:\.\d+)?\b",
        # Dates / Times
        "date_iso":        r"\d{4}-\d{2}-\d{2}",
        "date_us":         r"\d{1,2}/\d{1,2}/\d{2,4}",
        "time_24h":        r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:[:.][0-5]\d)?\b",
        # Misc
        "postal_us":       r"\b\d{5}(?:-\d{4})?\b",
        "postal_uk":       r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b",
        "html_color_hex":  r"#[0-9A-Fa-f]{6}\b",
        "twitter_handle":  r"@[\w]{1,15}",
        "hashtag":         r"#[\w-]+",
        "mac_addr":        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}",
        "iban":            r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}",
        "credit_card":     r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b",
    }

    _FLAGS = re.IGNORECASE | re.MULTILINE
    _UNWANTED_PROPS = {
        "provider": "Use llm_config instead",
        "api_token": "Use llm_config instead",
    }

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        pattern: _B = _B.NOTHING,
        *,
        custom: dict[str, str] | list[tuple[str, str]] | None = None,
        input_format: str = "fit_html",
        **kwargs,
    ) -> None:
        """
        Args:
            patterns: Custom patterns overriding or extending defaults.
                      Dict[label, regex] or list[tuple(label, regex)].
            input_format: "html", "markdown" or "text".
            **kwargs: Forwarded to ExtractionStrategy.
        """
        super().__init__(input_format=input_format, **kwargs)

        # 1️⃣  take only the requested built-ins
        merged: dict[str, str] = {
            key: rx
            for key, rx in self.DEFAULT_PATTERNS.items()
            if getattr(self._B, key.upper()).value & pattern
        }

        # 2️⃣  apply user overrides / additions
        if custom:
            if isinstance(custom, dict):
                merged.update(custom)
            else:  # iterable of (label, regex)
                merged.update({lbl: rx for lbl, rx in custom})

        self._compiled: dict[str, Pattern] = {
            lbl: re.compile(rx, self._FLAGS) for lbl, rx in merged.items()
        }

    # ------------------------------------------------------------------ #
    # Extraction
    # ------------------------------------------------------------------ #
    def extract(self, url: str, content: str, *q, **kw) -> list[dict[str, Any]]:
        # text = self._plain_text(html)
        out: list[dict[str, Any]] = []

        for label, cre in self._compiled.items():
            for m in cre.finditer(content):
                out.append(
                    {
                        "url": url,
                        "label": label,
                        "value": m.group(0),
                        "span": [m.start(), m.end()],
                    }
                )
        return out

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _plain_text(self, content: str) -> str:
        if self.input_format == "text":
            return content
        return BeautifulSoup(content, "lxml").get_text(" ", strip=True)

