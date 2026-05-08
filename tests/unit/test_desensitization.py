import pytest
from app.security.desensitization import DesensitizationEngine, get_desensitization_engine


class TestDesensitizationByCategory:
    
    @pytest.mark.parametrize("input_text,expected_output", [
        ("身份证号110101199001011234", "身份证号********************"),
        ("身份证320102197806067890", "身份证********************"),
        ("患者张三，身份证号440301199203154567", "患者PATIENT_张三，身份证号********************"),
    ], ids=["完整身份证号", "身份证号前缀", "身份证号在句子中"])
    def test_id_card_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("手机号13812345678", "手机号******5678"),
        ("电话13987654321", "电话******4321"),
        ("13611112222", "******2222"),
        ("患者张三，手机15899998888", "患者PATIENT_张三，手机******8888"),
    ], ids=["手机号前缀", "电话前缀", "纯手机号", "手机号在句子中"])
    def test_phone_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("患者张三", "患者PATIENT_张三"),
        ("李四女士", "PATIENT_李四女士"),
        ("王五先生", "PATIENT_王五先生"),
        ("孙七同志", "PATIENT_孙七同志"),
        ("患者姓名：刘十三", "患者姓名：PATIENT_刘十三"),
    ], ids=["患者前缀", "女士称谓", "先生称谓", "同志称谓", "患者姓名标签"])
    def test_patient_name_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("邮箱zhou@hospital.com", "邮箱EMAIL_***"),
        ("email: test@example.cn", "email: EMAIL_***"),
        ("用户邮箱是user@test.com", "用户邮箱是EMAIL_***"),
    ], ids=["邮箱前缀", "email标签", "句子中的邮箱"])
    def test_email_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("IP地址192.168.1.100", "IP地址***.***.***.***"),
        ("IP 10.0.0.1", "IP ***.***.***.***"),
        ("服务器IP：172.16.0.1", "服务器IP：***.***.***.***"),
    ], ids=["IP地址前缀", "IP前缀", "IP标签"])
    def test_ip_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("1995-05-15出生", "1995-**-**出生"),
        ("出生日期：1985-08-20", "出生日期：1985-**-**"),
        ("出生年月1990-03-20", "出生年月1990-**-**"),
    ], ids=["日期在句子中", "出生日期标签", "出生年月标签"])
    def test_date_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("住址北京市朝阳区建国路88号", "住址北京市***"),
        ("地址深圳市南山区科技园", "地址深圳市***"),
        ("住址：四川省成都市武侯区", "住址：四川省成都市***"),
    ], ids=["住址前缀", "地址前缀", "住址标签"])
    def test_address_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
        assert result == expected_output

    @pytest.mark.parametrize("input_text,expected_output", [
        ("病历号MR2024001234", "病历号MR********"),
        ("MR20240507001", "MR********"),
        ("住院号20240507001", "住院号20240507001"),
    ], ids=["病历号前缀", "纯病历号", "住院号"])
    def test_medical_record_desensitization(self, input_text, expected_output):
        engine = get_desensitization_engine()
        result = engine.desensitize(input_text)
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


class TestDesensitizationComplex:
    
    @pytest.mark.parametrize("input_text,expected_output", [
        ("患者张三，45岁，因甲状腺结节就诊，身份证号110101198005051234", 
         "患者PATIENT_张三，45岁，因甲状腺结节就诊，身份证号********************"),
        ("李四女士，35岁，肝脏超声检查，手机号13812345678，邮箱li@hospital.com", 
         "PATIENT_李四女士，35岁，肝脏超声检查，手机号******5678，邮箱EMAIL_***"),
        ("王五先生，55岁，心脏彩超，住址北京市朝阳区，IP地址192.168.1.101", 
         "PATIENT_王五先生，55岁，心脏彩超，住址北京市***，IP地址***.***.***.***"),
    ], ids=["复杂病历1", "复杂病历2", "复杂病历3"])
    def test_complex_cases(self, input_text, expected_output):
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

    def test_desensitize_none(self):
        engine = get_desensitization_engine()
        assert engine.desensitize(None) is None

    def test_desensitize_dict(self):
        engine = get_desensitization_engine()
        test_data = {
            "patient_name": "张三",
            "id_card": "110101199001011234",
            "phone": "13812345678",
            "address": "北京市朝阳区",
            "details": {
                "age": 45,
                "birth_date": "1980-05-05",
                "contact": {
                    "email": "zhang@hospital.com",
                    "ip": "192.168.1.100"
                }
            },
            "records": ["MR20240507001", "MR20240507002"]
        }
        
        result = engine.desensitize_dict(test_data)
        
        assert result["patient_name"] == "PATIENT_张三"
        assert result["id_card"] == "********************"
        assert result["phone"] == "******5678"
        assert result["address"] == "北京市***"
        assert result["details"]["age"] == 45
        assert result["details"]["birth_date"] == "1980-**-**"
        assert result["details"]["contact"]["email"] == "EMAIL_***"
        assert result["details"]["contact"]["ip"] == "***.***.***.***"
        assert result["records"] == ["MR********", "MR********"]