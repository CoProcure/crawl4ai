import re

from html2text import HTML2Text
from html2text import config as html2text_config
from html2text.utils import dumb_css_parser, hn


class CustomHTML2Text(HTML2Text):
    def __init__(self, *args, handle_code_in_pre=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.inside_pre = False
        self.inside_code = False
        self.inside_link = False
        self.preserve_tags = set()
        self.current_preserved_tag = None
        self.preserved_content = []
        self.preserve_depth = 0
        self.handle_code_in_pre = handle_code_in_pre

        self.skip_internal_links = False
        self.single_line_break = False
        self.mark_code = False
        self.include_sup_sub = False
        self.body_width = 0
        self.ignore_mailto_links = True
        self.ignore_links = False
        self.escape_snob = False

    def update_params(self, **kwargs):
        for key, value in kwargs.items():
            if key == "preserve_tags":
                self.preserve_tags = set(value)
            else:
                setattr(self, key, value)

    @staticmethod
    def _open_tag(tag, attrs):
        attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items() if v is not None)
        return f"<{tag}{attr_str}>"

    def handle_tag(self, tag, attrs, start):
        if tag in self.preserve_tags:
            if start:
                if self.preserve_depth == 0:
                    self.current_preserved_tag = tag
                    self.preserved_content = [self._open_tag(tag, attrs)]
                self.preserve_depth += 1
            else:
                self.preserve_depth -= 1
                if self.preserve_depth == 0:
                    self.preserved_content.append(f"</{tag}>")
                    self.o("\n" + "".join(self.preserved_content) + "\n")
                    self.current_preserved_tag = None
            return

        if self.preserve_depth > 0:
            self.preserved_content.append(
                self._open_tag(tag, attrs) if start else f"</{tag}>"
            )
            return

        if tag == "pre":
            if start:
                self.o("```\n")
                self.inside_pre = True
            else:
                self.o("\n```\n")
                self.inside_pre = False
        elif tag == "code":
            if self.inside_pre and not self.handle_code_in_pre:
                return
            if not self.inside_link:
                self.o("`")
            self.inside_code = start
            if self.inside_link:
                super().handle_tag(tag, attrs, start)
        else:
            paragraph_state_before = self.p_p
            super().handle_tag(tag, attrs, start)
            self._adjust_after_upstream_tag(tag, start, paragraph_state_before)

    def _adjust_after_upstream_tag(self, tag, start, paragraph_state_before):
        # Upstream html2text doesn't track whether it is inside a link, so we track it ourselves.
        if tag == "a" and not self.ignore_links:
            self.inside_link = start

        # Upstream html2text separates definition-list entries with paragraph breaks, so each branch resets that state.
        elif tag == "dl" and start:
            self.p_p = 0
        elif tag == "dt" and start:
            if self.p_p == 0:
                self.o("\n\n")
            self.p_p = 0
        elif tag == "dt" and not start:
            self.p_p = paragraph_state_before
            self.o("\n")
        elif tag == "dd" and not start:
            self.p_p = 0

    def handle_data(self, data, entity_char=False):
        if self.preserve_depth > 0:
            self.preserved_content.append(data)
            return

        if self.inside_pre:
            self.o(data)
            return
        if self.inside_code:
            self.o(data.replace("\n", " "))
            return

        if not data:
            return

        if self.stressed:
            data = data.strip()
            self.stressed = False
            self.preceding_stressed = True
        elif self.preceding_stressed:
            if (
                re.match(r"[^][(){}\s.!?]", data[0])
                and not hn(self.current_tag)
                and self.current_tag not in ["a", "code", "pre"]
            ):
                data = " " + data
            self.preceding_stressed = False

        if self.style:
            self.style_def.update(dumb_css_parser(data))

        if self.maybe_automatic_link is not None:
            href = self.maybe_automatic_link
            if (
                href == data
                and self.absolute_url_matcher.match(href)
                and self.use_automatic_links
            ):
                self.o("<" + data + ">")
                self.empty_link = False
                return
            else:
                self.o("[")
                self.maybe_automatic_link = None
                self.empty_link = False

        if not self.code and not self.pre and not entity_char:
            data = html2text_config.RE_MD_BACKSLASH_MATCHER.sub(r"\\\1", data)
        self.preceding_data = data
        self.o(data, puredata=True)
