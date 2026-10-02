import asyncio

import structlog
from sqlalchemy import delete, select

from safety_gateway.sg_db import get_db_session
from safety_gateway.sg_models import OrganDiseaseThreshold

logger = structlog.get_logger()


class ThresholdPersistence:
    """阈值持久化组件

    负责将阈值变更持久化到PostgreSQL。
    从ThresholdEngine中拆分出来，降低单文件复杂度。
    """

    @staticmethod
    async def persist_to_db(organ: str, threshold: float, disease_type: str | None = None) -> None:
        try:
            async with get_db_session() as session:
                if disease_type:
                    result = await session.execute(
                        select(OrganDiseaseThreshold)
                        .where(OrganDiseaseThreshold.organ == organ)
                        .where(OrganDiseaseThreshold.disease_type == disease_type)
                    )
                    existing = result.scalar_one_or_none()

                    if existing:
                        existing.confidence_threshold = threshold
                        existing.description = f"{organ}-{disease_type}动态更新阈值"
                    else:
                        new_threshold = OrganDiseaseThreshold(
                            organ=organ,
                            disease_type=disease_type,
                            confidence_threshold=threshold,
                            description=f"{organ}-{disease_type}动态更新阈值",
                        )
                        session.add(new_threshold)
                else:
                    result = await session.execute(
                        select(OrganDiseaseThreshold)
                        .where(OrganDiseaseThreshold.organ == organ)
                        .where(OrganDiseaseThreshold.disease_type.is_(None))
                    )
                    existing = result.scalar_one_or_none()

                    if existing:
                        existing.confidence_threshold = threshold
                        existing.description = f"{organ}动态更新阈值"
                    else:
                        new_threshold = OrganDiseaseThreshold(
                            organ=organ,
                            confidence_threshold=threshold,
                            description=f"{organ}动态更新阈值",
                        )
                        session.add(new_threshold)

                logger.info(
                    "sg_threshold_persisted_to_db",
                    organ=organ,
                    disease_type=disease_type,
                    threshold=threshold,
                )
        except Exception as e:
            logger.error(
                "sg_threshold_persist_failed",
                organ=organ,
                disease_type=disease_type,
                error=str(e),
            )
            raise

    @staticmethod
    async def delete_from_db(organ: str, disease_type: str | None = None) -> None:
        try:
            async with get_db_session() as session:
                if disease_type:
                    await session.execute(
                        delete(OrganDiseaseThreshold)
                        .where(OrganDiseaseThreshold.organ == organ)
                        .where(OrganDiseaseThreshold.disease_type == disease_type)
                    )
                else:
                    await session.execute(
                        delete(OrganDiseaseThreshold)
                        .where(OrganDiseaseThreshold.organ == organ)
                        .where(OrganDiseaseThreshold.disease_type.is_(None))
                    )

                logger.info(
                    "sg_threshold_deleted_from_db",
                    organ=organ,
                    disease_type=disease_type,
                )
        except Exception as e:
            logger.error(
                "sg_threshold_delete_failed",
                organ=organ,
                disease_type=disease_type,
                error=str(e),
            )

    @staticmethod
    def handle_persist_result(task: "asyncio.Task") -> None:
        try:
            task.result()
        except Exception as e:
            logger.error(
                "sg_threshold_persist_task_failed",
                error=str(e),
            )
