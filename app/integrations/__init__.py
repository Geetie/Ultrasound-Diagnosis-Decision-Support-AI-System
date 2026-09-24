"""外部系统适配器包（可注入 + 本地假实现）。

与 EDG / TensorRT 的既有处理方式一致：外部系统（PACS/HIS、对象存储）一律
通过 Protocol + 工厂 + settings 开关接入，本地用假实现验证编排、重试、
降级与幂等，现场只需注入真实实现。

子包按需导入（``app.integrations.pacs``、``app.integrations.object_storage``），
此处不做聚合导入，避免任一子包的可选依赖缺失时拖垮整包。
"""
