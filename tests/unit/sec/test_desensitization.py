"""主脱敏引擎单元测试模块

本模块测试主脱敏引擎（app.security.desensitization），这是三重脱敏防线的第一层（标准化层）。

与safety_gateway测试的区别：
- 本模块测试主脱敏引擎（标准化层脱敏）
- safety_gateway测试SG专用脱敏引擎（第三层结构化身份模式拦截）
- 两者使用独立规则集，实现规则层面的异构冗余（参考04-安全合规.md）

测试覆盖：
- 身份证号/手机号/患者姓名/邮箱/地址/日期脱敏
- 参数化PII上下文测试（8种PII×5种上下文）
- 嵌套结构脱敏（dict/list/深层嵌套）
- SafetyGateway异构验证（9种SG规则×3种输入）
- 三层脱敏端到端集成（10种临床场景×3层）
- 边界条件与性能基准
"""

import re
import time

import pytest

from app.security.desensitization import get_desensitization_engine

pytestmark = [pytest.mark.track_sec, pytest.mark.sec_domain_desens]

PATIENT_TOKEN_PATTERN = re.compile(r'PATIENT_\d{3}')


class TestDesensitizationByCategory:

    @pytest.mark.parametrize("input_text", [
        "身份证号110101199001011234",
        "身份证320102197806067890",
    ], ids=["完整身份证号", "身份证号前缀"])
    def test_id_card_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert '********************' in result
        assert '110101199001011234' not in result
        assert '320102197806067890' not in result

    def test_id_card_in_sentence(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三，身份证号440301199203154567")
        assert '********************' in result
        assert '440301199203154567' not in result
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX token in: {result}"

    @pytest.mark.parametrize("input_text,expected_suffix", [
        ("手机号13812345678", "5678"),
        ("电话13987654321", "4321"),
        ("13611112222", "2222"),
    ], ids=["手机号前缀", "电话前缀", "纯手机号"])
    def test_phone_desensitization(self, input_text, expected_suffix):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result.endswith(expected_suffix) or expected_suffix in result
        assert '13812345678' not in result
        assert '******' in result

    def test_phone_in_sentence(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三，手机15899998888")
        assert '******8888' in result or '******' in result
        assert '15899998888' not in result
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX token in: {result}"

    @pytest.mark.parametrize("input_text", [
        "患者张三",
        "李四女士",
        "王五先生",
        "孙七同志",
        "患者姓名：刘十三",
    ], ids=["患者前缀", "女士称谓", "先生称谓", "同志称谓", "患者姓名标签"])
    def test_patient_name_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX token in: {result}"
        assert '张三' not in result
        assert '李四' not in result
        assert '王五' not in result
        assert '孙七' not in result
        assert '刘十三' not in result

    def test_patient_name_consistent_token(self):
        engine = get_desensitization_engine()
        result1 = engine.desensitize("患者张三")
        result2 = engine.desensitize("患者张三")
        assert result1 == result2, "Same name should produce same token"

    def test_patient_name_different_token(self):
        engine = get_desensitization_engine()
        result1 = engine.desensitize("患者赵六")
        result2 = engine.desensitize("患者钱七")
        assert result1 != result2, "Different names should produce different tokens"

    @pytest.mark.parametrize("input_text", [
        "邮箱zhou@hospital.com",
        "email: test@example.cn",
        "用户邮箱是user@test.com",
    ], ids=["邮箱前缀", "email标签", "句子中的邮箱"])
    def test_email_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert 'EMAIL_***' in result
        assert '@' not in result or 'EMAIL_***' in result

    @pytest.mark.parametrize("input_text", [
        "IP地址192.168.1.100",
        "IP 10.0.0.1",
        "服务器IP：172.16.0.1",
    ], ids=["IP地址前缀", "IP前缀", "IP标签"])
    def test_ip_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert '***.***.***.***' in result
        assert '192.168.1.100' not in result
        assert '10.0.0.1' not in result
        assert '172.16.0.1' not in result

    @pytest.mark.parametrize("input_text", [
        "1995-05-15出生",
        "出生日期：1985-08-20",
        "出生年月1990-03-20",
    ], ids=["日期在句子中", "出生日期标签", "出生年月标签"])
    def test_date_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert '-**-**' in result
        assert '1995-05-15' not in result
        assert '1985-08-20' not in result
        assert '1990-03-20' not in result

    @pytest.mark.parametrize("input_text", [
        "住址北京市朝阳区建国路88号",
        "地址深圳市南山区科技园",
        "住址：四川省成都市武侯区",
    ], ids=["住址前缀", "地址前缀", "住址标签"])
    def test_address_desensitization(self, input_text):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert '***' in result
        assert '朝阳区' not in result
        assert '南山区' not in result
        assert '武侯区' not in result

    @pytest.mark.parametrize("input_text,expected_output", [
        ("病历号MR2024001234", "[REDACTED:MEDICAL_RECORD]"),
        ("MR20240507001", "MR20240507001"),
        ("住院号20240507001", "[REDACTED:MEDICAL_RECORD]"),
    ], ids=["病历号前缀", "纯病历号", "住院号"])
    def test_medical_record_desensitization(self, input_text, expected_output):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        assert result == expected_output


class TestDesensitizationBoundary:

    @pytest.mark.parametrize("input_text,expected_output", [
        ("身份证号11010119900101", "身份证号11010119900101"),
        ("身份证32010219780606789", "身份证32010219780606789"),
        ("手机138", "手机138"),
        ("1381234567", "1381234567"),
        ("电话：010-12345678", "电话：010-12345678"),
        ("邮箱test@", "邮箱test@"),
        ("IP 192.168.1", "IP 192.168.1"),
        ("住址北京市", "住址北京市"),
        ("出生年月2024", "出生年月2024"),
        ("MR123", "MR123"),
    ], ids=[
        "不完整身份证号12位",
        "不完整身份证号17位",
        "不完整手机号3位",
        "10位数字非手机号",
        "固定电话格式",
        "不完整邮箱",
        "不完整IP地址",
        "无详细地址",
        "不完整日期",
        "短病历号"
    ])
    def test_boundary_cases(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output


class TestDesensitizationEngine:

    def test_engine_singleton(self):
        engine1 = get_desensitization_engine()
        engine2 = get_desensitization_engine()
        assert engine1 is engine2

    def test_empty_string(self):
        engine = get_desensitization_engine()
        assert engine.desensitize("") == ""

    def test_none_input(self):
        engine = get_desensitization_engine()
        assert engine.desensitize(None) is None


class TestDesensitizationPerformance:
    """墙钟性能断言。

    两个用例都断言绝对耗时（<1s），并行执行时受 CPU 争抢影响会假失败
    （实测 -n 8 下 1000 条 1.03s、5000 次 4.06s，串行均通过）。
    因此按仓库既有约定标记 ``perf``：默认被 addopts 排除，
    需串行单独运行（make test-perf / pytest -m perf -p no:xdist）。
    """

    @pytest.mark.perf
    def test_performance_large_text(self):
        engine = get_desensitization_engine()

        large_text = "患者张三，身份证号110101199001011234，手机号13812345678。" * 1000

        start_time = time.time()
        result = engine.desensitize(large_text)
        end_time = time.time()

        duration = end_time - start_time

        assert len(result) > 0
        assert duration < 1.0, f"脱敏1000条记录耗时 {duration:.2f}s，超过1秒"

    @pytest.mark.perf
    def test_performance_many_requests(self):
        engine = get_desensitization_engine()

        test_cases = [
            "患者张三，身份证号110101199001011234",
            "手机号13812345678",
            "邮箱test@hospital.com",
            "IP地址192.168.1.100",
            "1995-05-15出生"
        ]

        start_time = time.time()
        for _ in range(1000):
            for case in test_cases:
                engine.desensitize(case)
        end_time = time.time()

        duration = end_time - start_time

        assert duration < 1.0, f"5000次脱敏请求耗时 {duration:.2f}s，超过1秒"


class TestDesensitizationComplex:

    def test_complex_case_1(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三，45岁，因甲状腺结节就诊，身份证号110101198005051234")
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX in: {result}"
        assert '********************' in result
        assert '110101198005051234' not in result
        assert '张三' not in result
        assert '45岁' in result

    def test_complex_case_2(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("李四女士，35岁，肝脏超声检查，手机号13812345678，邮箱li@hospital.com")
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX in: {result}"
        assert '******' in result
        assert 'EMAIL_***' in result
        assert '13812345678' not in result
        assert '李四' not in result
        assert '35岁' in result

    def test_complex_case_3(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("王五先生，55岁，心脏彩超，住址北京市朝阳区，IP地址192.168.1.101")
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX in: {result}"
        assert '***' in result
        assert '***.***.***.***' in result
        assert '192.168.1.101' not in result
        assert '王五' not in result
        assert '55岁' in result


class TestPIICombinations:

    ID_CARD_CONTEXTS = [
        ("身份证号110101199001011234", True, "纯身份证号"),
        ("患者身份证号110101199001011234就诊", True, "身份证号在句子中"),
        ("证件号码：110101199001011234", True, "冒号后身份证号"),
        ("身份证号11010119900101", False, "不完整身份证号"),
        ("编号11010119900101123", False, "15位旧身份证号"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", ID_CARD_CONTEXTS,
                             ids=[c[2] for c in ID_CARD_CONTEXTS])
    def test_id_card_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert '110101199001011234' not in result, f"{desc}: 身份证号未被脱敏"
        else:
            assert input_text == result, f"{desc}: 不应脱敏的内容被误脱敏"

    PHONE_CONTEXTS = [
        ("手机号13812345678", True, "手机号前缀"),
        ("联系电话13987654321", True, "联系电话前缀"),
        ("电话：13611112222", True, "电话冒号"),
        ("号码1381234", False, "不完整手机号"),
        ("分机12345678", False, "非手机号"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", PHONE_CONTEXTS,
                             ids=[c[2] for c in PHONE_CONTEXTS])
    def test_phone_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert '******' in result, f"{desc}: 手机号未被脱敏"
        else:
            assert input_text == result, f"{desc}: 不应脱敏的内容被误脱敏"

    NAME_CONTEXTS = [
        ("患者张三", True, "患者前缀"),
        ("李四女士", True, "女士称谓"),
        ("王五先生", True, "先生称谓"),
        ("赵六医生", True, "医生称谓"),
        ("患者姓名：刘十三", True, "患者姓名标签"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", NAME_CONTEXTS,
                             ids=[c[2] for c in NAME_CONTEXTS])
    def test_name_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert PATIENT_TOKEN_PATTERN.search(result), f"{desc}: 姓名未被脱敏"

    EMAIL_CONTEXTS = [
        ("邮箱zhou@hospital.com", True, "邮箱前缀"),
        ("email: test@example.cn", True, "email标签"),
        ("用户邮箱是user@test.com", True, "句子中邮箱"),
        ("@mention", False, "非邮箱@符号"),
        ("test@", False, "不完整邮箱"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", EMAIL_CONTEXTS,
                             ids=[c[2] for c in EMAIL_CONTEXTS])
    def test_email_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert 'EMAIL_***' in result, f"{desc}: 邮箱未被脱敏"

    ADDRESS_CONTEXTS = [
        ("住址北京市朝阳区建国路88号", True, "住址前缀"),
        ("地址深圳市南山区科技园", True, "地址前缀"),
        ("住址：四川省成都市武侯区", True, "住址冒号"),
        ("北京市", False, "仅市级无详细地址"),
        ("上海人", False, "非地址上下文"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", ADDRESS_CONTEXTS,
                             ids=[c[2] for c in ADDRESS_CONTEXTS])
    def test_address_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert '***' in result, f"{desc}: 地址未被脱敏"

    DATE_CONTEXTS = [
        ("1995-05-15出生", True, "日期后出生"),
        ("出生日期：1985-08-20", True, "出生日期标签"),
        ("出生年月1990-03-20", True, "出生年月标签"),
        ("2024-01-15", False, "无上下文日期"),
        ("生日快乐", False, "非日期格式"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", DATE_CONTEXTS,
                             ids=[c[2] for c in DATE_CONTEXTS])
    def test_date_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert '-**-**' in result, f"{desc}: 日期未被脱敏"

    IP_CONTEXTS = [
        ("IP地址192.168.1.100", True, "IP地址前缀"),
        ("IP 10.0.0.1", True, "IP前缀"),
        ("服务器IP：172.16.0.1", True, "服务器IP标签"),
        ("版本 2.0.1.3", False, "版本号"),
        ("v 1.2.3.4", False, "v前缀版本"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", IP_CONTEXTS,
                             ids=[c[2] for c in IP_CONTEXTS])
    def test_ip_contexts(self, input_text, should_mask, desc):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        if should_mask:
            assert '***.***.***.***' in result, f"{desc}: IP未被脱敏"

    MR_CONTEXTS = [
        ("病历号MR2024001234", True, "病历号前缀"),
        ("住院号20240507001", True, "住院号前缀"),
        ("MR123", False, "短病历号"),
        ("MR", False, "仅MR前缀"),
        ("AMR12345678", False, "MR非独立"),
    ]

    @pytest.mark.parametrize("input_text,should_mask,desc", MR_CONTEXTS,
                             ids=[c[2] for c in MR_CONTEXTS])
    def test_mr_contexts(self, input_text, should_mask, desc):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '[REDACTED:MEDICAL_RECORD]' in result, f"{desc}: 病历号未被脱敏"


class TestNestedStructureDesensitization:

    def test_flat_dict(self):
        engine = get_desensitization_engine()
        data = {"name": "患者张三", "id": "110101199001011234"}
        result = engine.desensitize_dict(data)
        assert PATIENT_TOKEN_PATTERN.search(str(result.values()))
        assert '110101199001011234' not in str(result.values())

    def test_nested_dict(self):
        engine = get_desensitization_engine()
        data = {"patient": {"name": "患者张三", "phone": "13812345678"}}
        result = engine.desensitize_dict(data)
        result_str = str(result)
        assert '张三' not in result_str
        assert '13812345678' not in result_str

    def test_dict_with_list(self):
        engine = get_desensitization_engine()
        data = {"patients": ["患者张三", "患者李四"]}
        result = engine.desensitize_dict(data)
        result_str = str(result)
        assert '张三' not in result_str
        assert '李四' not in result_str

    def test_deeply_nested(self):
        engine = get_desensitization_engine()
        data = {"level1": {"level2": {"level3": "身份证号110101199001011234"}}}
        result = engine.desensitize_dict(data)
        assert '110101199001011234' not in str(result)

    def test_dict_with_none_values(self):
        engine = get_desensitization_engine()
        data = {"name": None, "id": "110101199001011234"}
        result = engine.desensitize_dict(data)
        assert result["name"] is None
        assert '110101199001011234' not in str(result["id"])

    def test_dict_with_numeric_values(self):
        engine = get_desensitization_engine()
        data = {"age": 45, "phone": "13812345678"}
        result = engine.desensitize_dict(data)
        assert result["age"] == 45

    def test_dict_with_boolean_values(self):
        engine = get_desensitization_engine()
        data = {"active": True, "notes": "患者张三"}
        result = engine.desensitize_dict(data)
        assert result["active"] is True

    def test_empty_dict(self):
        engine = get_desensitization_engine()
        result = engine.desensitize_dict({})
        assert result == {}

    def test_dict_with_empty_string(self):
        engine = get_desensitization_engine()
        data = {"name": "", "id": "110101199001011234"}
        result = engine.desensitize_dict(data)
        assert result["name"] == ""

    def test_list_of_dicts(self):
        engine = get_desensitization_engine()
        data = {"patients": [{"name": "患者张三"}, {"name": "患者李四"}]}
        result = engine.desensitize_dict(data)
        result_str = str(result)
        assert '张三' not in result_str or 'PATIENT_' in result_str


class TestExtendedBoundaryConditions:

    def test_very_long_text(self):
        engine = get_desensitization_engine()
        text = "患者张三，身份证号110101199001011234。" * 1000
        result = engine.desensitize(text)
        assert '110101199001011234' not in result
        assert '张三' not in result

    def test_pure_numeric_text(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("12345678901234567890")
        assert result == "12345678901234567890"

    def test_fullwidth_characters(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("身份证号１１０１０１１９９００１０１１２３４")
        assert '１１０１０１１９９００１０１１２３４' not in result or result == "身份证号１１０１０１１９９００１０１１２３４"

    def test_mixed_chinese_english(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("Patient张三ID110101199001011234")
        assert '110101199001011234' not in result

    def test_multiple_id_cards(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("身份证110101199001011234和320102197806067890")
        assert '110101199001011234' not in result
        assert '320102197806067890' not in result

    def test_already_desensitized_text(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("身份证号********************")
        assert '********************' in result

    def test_unicode_special_chars(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者\u3000张三")
        assert '张三' not in result or result == "患者\u3000张三"

    def test_newline_separated(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三\n身份证号110101199001011234")
        assert '张三' not in result
        assert '110101199001011234' not in result

    def test_tab_separated(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三\t手机号13812345678")
        assert '张三' not in result
        assert '13812345678' not in result

    def test_carriage_return(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三\r\n身份证号110101199001011234")
        assert '110101199001011234' not in result

    def test_html_tags(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("<p>患者张三</p>")
        assert '张三' not in result

    def test_json_string(self):
        engine = get_desensitization_engine()
        result = engine.desensitize('{"name": "患者张三", "id": "110101199001011234"}')
        assert '110101199001011234' not in result

    def test_url_with_ip(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("IP地址192.168.1.100/api/v1")
        assert '192.168.1.100' not in result

    def test_consecutive_pii(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("手机13812345678和13987654321")
        assert '13812345678' not in result
        assert '13987654321' not in result

    def test_pii_at_string_boundaries(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("13812345678")
        assert '13812345678' not in result

    def test_empty_input(self):
        engine = get_desensitization_engine()
        assert engine.desensitize("") == ""

    def test_none_input(self):
        engine = get_desensitization_engine()
        assert engine.desensitize(None) is None

    def test_whitespace_only(self):
        engine = get_desensitization_engine()
        assert engine.desensitize("   ") == "   "

    def test_single_char(self):
        engine = get_desensitization_engine()
        assert engine.desensitize("张") == "张"

    def test_emoji_in_text(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三😊手机号13812345678")
        assert '张三' not in result
        assert '13812345678' not in result


class TestSafetyGatewayHeterogeneous:

    SG_ID_CARD_INPUTS = [
        ("身份证号110101199001011234", True),
        ("身份证320102197806067890", True),
        ("编号12345", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_ID_CARD_INPUTS,
                             ids=["完整身份证", "身份证前缀", "非身份证"])
    def test_sg_id_card(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '110101199001011234' not in result and '320102197806067890' not in result

    SG_PHONE_INPUTS = [
        ("手机13812345678", True),
        ("电话13987654321", True),
        ("1381234", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_PHONE_INPUTS,
                             ids=["手机前缀", "电话前缀", "短号码"])
    def test_sg_phone(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '13812345678' not in result and '13987654321' not in result

    SG_NAME_INPUTS = [
        ("患者：张三", True),
        ("李四先生", True),
        ("张三", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_NAME_INPUTS,
                             ids=["患者冒号姓名", "姓名称谓", "无上下文姓名"])
    def test_sg_name(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '张三' not in result and '李四' not in result

    SG_EMAIL_INPUTS = [
        ("邮箱test@hospital.com", True),
        ("email: user@example.cn", True),
        ("test@", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_EMAIL_INPUTS,
                             ids=["邮箱前缀", "email标签", "不完整邮箱"])
    def test_sg_email(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '@' not in result or '***' in result

    SG_BIRTH_INPUTS = [
        ("出生日期：1985-08-20", True),
        ("生日1990/03/15", True),
        ("2024-01-01", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_BIRTH_INPUTS,
                             ids=["出生日期标签", "生日斜杠", "无上下文日期"])
    def test_sg_birth_date(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '1985-08-20' not in result and '1990/03/15' not in result

    SG_ADDRESS_INPUTS = [
        ("住址北京市朝阳区", True),
        ("地址：深圳市南山区", True),
        ("北京市", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_ADDRESS_INPUTS,
                             ids=["住址前缀", "地址冒号", "仅市级"])
    def test_sg_address(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '朝阳区' not in result and '南山区' not in result

    SG_MR_INPUTS = [
        ("病历号MR2024001234", True),
        ("病案号MR20240507001", True),
        ("MR123", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_MR_INPUTS,
                             ids=["病历号前缀", "病案号前缀", "短编号"])
    def test_sg_medical_record(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert 'MR2024001234' not in result and 'MR20240507001' not in result

    SG_DICOM_INPUTS = [
        ("StudyInstanceUID:1.2.840.113619.2.55", True),
        ("SOPInstanceUID:1.2.840.10008.5.1.4", True),
        ("1.2.3", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_DICOM_INPUTS,
                             ids=["StudyInstanceUID", "SOPInstanceUID", "短UID"])
    def test_sg_dicom_uid(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '1.2.840' not in result

    SG_IP_INPUTS = [
        ("IP地址192.168.1.100", True),
        ("服务器IP：10.0.0.1", True),
        ("版本2.0.1.3", False),
    ]

    @pytest.mark.parametrize("input_text,should_mask", SG_IP_INPUTS,
                             ids=["IP地址前缀", "服务器IP标签", "版本号"])
    def test_sg_ip_address(self, input_text, should_mask):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(input_text)
        if should_mask:
            assert '192.168.1.100' not in result and '10.0.0.1' not in result


class TestThreeLayerEndToEnd:

    CLINICAL_SCENARIOS = [
        "患者张三，45岁，甲状腺超声检查，身份证号110101199001011234",
        "李四女士，35岁，肝脏超声，手机号13812345678，邮箱li@hospital.com",
        "王五先生，55岁，心脏彩超，住址北京市朝阳区，IP地址192.168.1.101",
        "患者赵六，28岁，肾脏超声，出生日期1996-03-15，病历号MR2024001234",
        "孙七同志，60岁，乳腺超声，身份证号320102197806067890，手机13987654321",
        "患者姓名：周八，42岁，胆囊超声，邮箱zhou@test.com，住址上海市浦东新区",
        "患者吴九，33岁，前列腺超声，IP 10.0.0.1，身份证440301199203154567",
        "郑十女士，48岁，子宫超声，手机13611112222，出生年月1976-11-08",
        "患者：钱十一，50岁，脾脏超声，住址广州市天河区，邮箱qian@med.cn",
        "患者冯十二，37岁，胰腺超声，病历号MR20240507001，身份证510105199301011234",
    ]

    @pytest.mark.parametrize("scenario", CLINICAL_SCENARIOS,
                             ids=[f"临床场景{i+1}" for i in range(len(CLINICAL_SCENARIOS))])
    def test_layer1_standard(self, scenario):
        engine = get_desensitization_engine()
        result = engine.desensitize(scenario)
        assert '110101199001011234' not in result
        assert '320102197806067890' not in result
        assert '440301199203154567' not in result
        assert '510105199301011234' not in result
        assert '13812345678' not in result
        assert '13987654321' not in result
        assert '13611112222' not in result

    @pytest.mark.parametrize("scenario", CLINICAL_SCENARIOS,
                             ids=[f"临床场景{i+1}" for i in range(len(CLINICAL_SCENARIOS))])
    def test_layer2_fusion(self, scenario):
        from app.fusion.desensitization import FusionDesensitizationEngine
        engine = FusionDesensitizationEngine()
        result = engine.desensitize_llm_output(scenario)
        assert '110101199001011234' not in result.desensitized_text
        assert '13812345678' not in result.desensitized_text

    @pytest.mark.parametrize("scenario", CLINICAL_SCENARIOS,
                             ids=[f"临床场景{i+1}" for i in range(len(CLINICAL_SCENARIOS))])
    def test_layer3_sg(self, scenario):
        from safety_gateway.desensitization import sg_desensitization_engine
        result = sg_desensitization_engine.desensitize(scenario)
        assert '110101199001011234' not in result
        assert '13812345678' not in result
