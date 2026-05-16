from app.config.database import AsyncSessionLocal


class WebSocketDBSession:
    def __init__(self):
        self._session = None

    async def get_session(self):
        if self._session is None:
            self._session = AsyncSessionLocal()
            await self._session.__aenter__()
        return self._session

    async def commit(self):
        if self._session:
            await self._session.commit()

    async def rollback(self):
        if self._session:
            await self._session.rollback()

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None
