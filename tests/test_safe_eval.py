"""Tests for ``crawl4ai.extraction_strategy._safe_eval_expression``.

These guard the CVE-2026-26216 fix: arbitrary expressions from a
user-supplied schema must never trigger code execution.
"""
import pytest

from crawl4ai.extraction_strategy import _safe_eval_expression


class TestArithmetic:
    def test_simple_arithmetic(self):
        assert _safe_eval_expression("price * qty", {"price": 2, "qty": 3}) == 6

    def test_compound_arithmetic(self):
        item = {"price": 100, "discount": 0.1}
        assert _safe_eval_expression(
            "price * (1 - discount)", item
        ) == pytest.approx(90.0)

    def test_attribute_access(self):
        class Bag:
            value = 42

        assert _safe_eval_expression("b.value + 1", {"b": Bag()}) == 43

    def test_subscript_access(self):
        assert _safe_eval_expression("d['x']", {"d": {"x": 7}}) == 7

    def test_comparison(self):
        assert _safe_eval_expression("a > b", {"a": 5, "b": 3}) is True

    def test_boolean_ops(self):
        assert _safe_eval_expression("a and b", {"a": 1, "b": 2}) == 2

    def test_fstring(self):
        assert _safe_eval_expression('f"{a}-{b}"', {"a": 1, "b": 2}) == "1-2"


class TestRcePayloadsBlocked:
    """Anything that could lead to code execution must raise."""

    @pytest.mark.parametrize(
        "payload",
        [
            "__import__('os').system('id')",
            "().__class__.__bases__[0].__subclasses__()",
            "open('/etc/passwd').read()",
            "eval('1+1')",
            "exec('x=1')",
            "[x for x in range(10)]",
            "{x: x for x in range(10)}",
            "(lambda: 1)()",
            "lambda x: x",
        ],
    )
    def test_blocks_payload(self, payload):
        with pytest.raises((ValueError, SyntaxError)):
            _safe_eval_expression(payload, {})

    def test_call_node_rejected_even_without_builtins(self):
        # Even if a name resolves, Call nodes are blocked at AST validation.
        item = {"f": lambda: "boom"}
        with pytest.raises(ValueError, match="Disallowed expression node"):
            _safe_eval_expression("f()", item)


class TestDosGuards:
    def test_pow_is_blocked(self):
        # `**` is a one-line CPU/memory bomb; must be rejected.
        with pytest.raises(ValueError, match="Disallowed expression node"):
            _safe_eval_expression("2 ** 1000", {})

    def test_oversized_literal_rejected(self):
        # `[0] * 9_999_999_999` would OOM the worker.
        with pytest.raises(ValueError, match="Numeric literal exceeds"):
            _safe_eval_expression("9999999999", {})

    def test_oversized_expression_rejected(self):
        long_expr = "1" + (" + 1" * 200)  # ~800 chars
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _safe_eval_expression(long_expr, {})


class TestInputValidation:
    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            _safe_eval_expression(123, {})

    def test_syntax_error_propagates(self):
        with pytest.raises(SyntaxError):
            _safe_eval_expression("1 +", {})

    def test_undefined_name_raises_name_error(self):
        with pytest.raises(NameError):
            _safe_eval_expression("undefined_name", {})
