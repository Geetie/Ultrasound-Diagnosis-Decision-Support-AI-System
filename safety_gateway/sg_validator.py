import os

from safety_gateway.sg_config import sg_settings


class SGConfigValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"SG Configuration validation failed: {', '.join(errors)}")


def validate_sg_config() -> list[str]:
    errors: list[str] = []

    if not sg_settings.redis_url:
        errors.append("REDIS_URL is required")

    redis_url = sg_settings.redis_url
    if redis_url and "/0" in redis_url.split("?")[0].rsplit("/", 1)[-1]:
        errors.append("REDIS_URL must use db=1 or higher (db=0 is reserved for main service)")

    if not sg_settings.database_url or sg_settings.database_url == "postgresql+asyncpg://admin:CHANGEME@localhost:5432/ultrasound_db":
        errors.append("DATABASE_URL must be explicitly set (default placeholder is not allowed)")

    for path_attr in (
        "desensitization_rules_safety_path",
        "contraindication_rules_safety_path",
    ):
        path = getattr(sg_settings, path_attr, "")
        if path and not os.path.isfile(path):
            errors.append(f"{path_attr}={path!r} does not exist (rules will fall back to hardcoded defaults)")

    return errors


def validate_sg_and_exit_on_error() -> None:
    errors = validate_sg_config()
    if errors:
        raise SGConfigValidationError(errors)
