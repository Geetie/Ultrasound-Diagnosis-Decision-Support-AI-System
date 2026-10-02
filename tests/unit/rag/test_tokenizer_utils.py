"""tokenizer 工具补测（quality-debt 覆盖率：0% -> 全覆盖）。

模块说明：`app/rag/tokenizer_utils.py` 目前**没有生产引用**（bm25_fallback 与
query_enhancer 各自带了同名实现）。这里仍然补测，原因是它是"设计上要统一的共享
工具"——一旦有人按注释改成引用它，行为必须有测试兜底，否则等于把一份没人验证过的
实现接进检索链路。

另外补测时发现 `tokenize_simple` 的 docstring 示例与实现不符（示例写的是 3 字窗口，
实现是 2 字窗口），已同步修正文档——错的示例比没有示例更危险。
"""

from app.rag.tokenizer_utils import (
    UDAFMaxMatchTokenizer,
    extract_chinese_phrases,
    tokenize_simple,
)


def _cjk_windows(phrase: str) -> list:
    """按实现口径给出"整段 + 每 2 字窗口"。"""
    return [phrase] + [phrase[i : i + 2] for i in range(len(phrase) - 1)]


class _FakeVocabulary:
    """鸭子类型替身：只需 iter_synonym_mappings / normalize / get_synonyms。"""

    def __init__(self, mappings=None, normalize_map=None, synonyms=None, explode=None):
        self._mappings = mappings or []
        self._normalize_map = normalize_map or {}
        self._synonyms = synonyms or {}
        self._explode = explode

    def iter_synonym_mappings(self):
        if self._explode == "iter":
            raise RuntimeError("vocab api mismatch")
        return iter(self._mappings)

    def normalize(self, term):
        if self._explode == "normalize":
            raise RuntimeError("normalize failed")
        return self._normalize_map.get(term, term)

    def get_synonyms(self, term):
        if self._explode == "synonyms":
            raise RuntimeError("get_synonyms failed")
        return list(self._synonyms.get(term, []))


class TestTokenizeSimple:
    def test_empty_text(self):
        assert tokenize_simple("") == []

    def test_cjk_phrase_emits_full_term_and_two_char_windows(self):
        assert tokenize_simple("低回声结节") == _cjk_windows("低回声结节")

    def test_alphanumeric_is_lowercased_and_split_on_punctuation(self):
        assert tokenize_simple("TI-RADS 4A nodule") == ["ti", "rads", "4a", "nodule"]

    def test_mixed_cjk_and_ascii(self):
        tokens = tokenize_simple("结节 TI-RADS")

        assert tokens[: len(_cjk_windows("结节"))] == _cjk_windows("结节")
        assert "ti" in tokens and "rads" in tokens

    def test_cjk_only_does_not_leak_into_ascii_tokens(self):
        """CJK 不属于 ASCII 字母数字，清洗后不应再产生独立的 CJK token。"""
        tokens = tokenize_simple("甲状腺")

        assert tokens == _cjk_windows("甲状腺")

    def test_single_cjk_char_has_no_window(self):
        assert tokenize_simple("结") == ["结"]

    def test_non_alnum_only_text_yields_empty(self):
        assert tokenize_simple("--- ...") == []

    def test_multiple_cjk_spans_are_tokenized_separately(self):
        tokens = tokenize_simple("结节,囊肿")

        assert "结节" in tokens
        assert "囊肿" in tokens
        assert len(tokens) == len(_cjk_windows("结节")) + len(_cjk_windows("囊肿"))


class TestUDAFMaxMatchTokenizerFallback:
    def test_without_vocabulary_uses_simple_tokenizer(self):
        tokenizer = UDAFMaxMatchTokenizer()

        assert tokenizer.tokenize("低回声结节") == tokenize_simple("低回声结节")

    def test_vocabulary_without_terms_uses_simple_tokenizer(self):
        """词表为空时最长匹配无从谈起，必须退回简单分词而不是返回空。"""
        tokenizer = UDAFMaxMatchTokenizer(_FakeVocabulary())

        assert tokenizer.tokenize("低回声结节") == tokenize_simple("低回声结节")

    def test_empty_text_returns_empty(self):
        tokenizer = UDAFMaxMatchTokenizer(_FakeVocabulary([("低回声", "低回声")]))

        assert tokenizer.tokenize("") == []

    def test_broken_vocabulary_api_degrades_silently(self):
        """vocab 接口不符时不得抛异常——降级为简单分词。"""
        tokenizer = UDAFMaxMatchTokenizer(_FakeVocabulary(explode="iter"))

        assert tokenizer.tokenize("低回声结节") == tokenize_simple("低回声结节")


class TestUDAFMaxMatch:
    def _tokenizer(self, **kwargs):
        vocab = _FakeVocabulary(
            mappings=[("低回声", "低回声"), ("微钙化", "微钙化")],
            **kwargs,
        )
        return UDAFMaxMatchTokenizer(vocab, max_term_len=4)

    def test_longest_match_preferred(self):
        tokenizer = self._tokenizer()

        tokens = tokenizer.tokenize("低回声结节")

        assert tokens[0] == "低回声"

    def test_normalized_term_emitted_alongside_match(self):
        """命中的同义词要一并产出标准词，否则检索召回不上。"""
        tokenizer = self._tokenizer(normalize_map={"低回声": "低回声团"})

        tokens = tokenizer.tokenize("低回声")

        assert "低回声" in tokens
        assert "低回声团" in tokens

    def test_synonyms_expanded(self):
        tokenizer = self._tokenizer(synonyms={"低回声": ["低回声区"]})

        tokens = tokenizer.tokenize("低回声")

        assert "低回声区" in tokens

    def test_synonym_equal_to_term_not_duplicated(self):
        tokenizer = self._tokenizer(synonyms={"低回声": ["低回声", "LOW"]})

        tokens = tokenizer.tokenize("低回声")

        assert tokens.count("低回声") == 1
        assert "low" in tokens

    def test_normalize_failure_is_swallowed(self):
        """归一化失败不能让整次分词崩掉——命中的词仍要产出。"""
        tokenizer = self._tokenizer(explode="normalize")

        tokens = tokenizer.tokenize("低回声")

        assert tokens == ["低回声"]

    def test_synonym_lookup_failure_is_swallowed(self):
        tokenizer = self._tokenizer(normalize_map={"低回声": "低回声团"}, explode="synonyms")

        tokens = tokenizer.tokenize("低回声")

        assert "低回声" in tokens and "低回声团" in tokens

    def test_unmatched_cjk_span_falls_back_to_window(self):
        tokenizer = self._tokenizer()

        tokens = tokenizer.tokenize("囊肿")

        assert "囊肿" in tokens

    def test_unmatched_ascii_run_collected(self):
        tokenizer = self._tokenizer()

        tokens = tokenizer.tokenize("nodule")

        assert "nodule" in tokens

    def test_punctuation_is_skipped(self):
        tokenizer = self._tokenizer()

        tokens = tokenizer.tokenize("低回声-nodule")

        assert "-" not in tokens
        assert "nodule" in tokens

    def test_max_term_len_only_bounds_max_match_not_fallback(self):
        """max_term_len 只约束"最长匹配"循环，**不**约束未命中时的 CJK 兜底。

        所以调小 max_term_len **不能**用来"禁止长出整段词"——整段词仍会由兜底分支
        输出。这是个容易误解的点（看名字像"词长上限"），用测试钉住真实语义。
        """
        vocab = _FakeVocabulary(mappings=[("低回声结节", "低回声结节")])
        short = UDAFMaxMatchTokenizer(vocab, max_term_len=2)

        tokens = short.tokenize("低回声结节")

        assert tokens == _cjk_windows("低回声结节"), "走的是兜底分支（整段 + 2 字窗口）"


class TestExtractChinesePhrases:
    def test_empty_text(self):
        assert extract_chinese_phrases("") == []

    def test_cjk_only_output(self):
        """注意输出顺序与 tokenize_simple **相反**：先 2 字窗口，最后才是整段。

        同一仓库里两个"同类工具"的顺序不一致，将来真要统一实现时这是必须处理的行为
        差异（顺序会影响 BM25 里 term 的位置权重），因此这里按实际行为钉住。
        """
        phrases = extract_chinese_phrases("TI-RADS 低回声结节")

        windows = ["低回声结节"[i : i + 2] for i in range(4)]
        assert phrases == [*windows, "低回声结节"]

    def test_no_cjk_returns_empty(self):
        assert extract_chinese_phrases("nodule TI-RADS") == []

    def test_multiple_spans(self):
        phrases = extract_chinese_phrases("结节和囊肿")

        # 中间夹了"和"，两段 CJK 分别成词
        assert "结节" in phrases
        assert "囊肿" in phrases
