from pathlib import Path
from pprint import pprint
from typing import AsyncIterator, Any
from uuid import uuid4, UUID

import httpx
from fastapi import FastAPI, Depends
from httpx import Response
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
# from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, Span
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings
from redis.asyncio.client import Redis
from redis.asyncio.sentinel import Sentinel
from starlette.middleware import Middleware
from starlette.status import HTTP_201_CREATED
from starlette_context import plugins, context
from starlette_context.middleware import ContextMiddleware

from .httpx_proxy_instrumentor import HTTPXClientInstrumentor

# CONFIGS
ROOT = Path().parent.parent


class AppConfig(BaseSettings):
    name: str = "project_230726"

    tracing_host: str | None = "127.0.0.1"
    tracing_port: int = 6831

    proxy: str | None = "http://localhost:1080"
    # proxy: str | None = None
    external_url: str = "https://eo9r8ho7t2jymrq.m.pipedream.net"

    class Config:
        env_file = ROOT / "local.env"
        env_prefix = "app_"


app_settings = AppConfig()
pprint(app_settings)


class RedisSettings(BaseSettings):
    host: str
    port: int
    password: str | None
    sentinel_master: str | None = None
    timeout: int = 1

    class Config:
        env_file = ROOT / "local.env"
        env_prefix = "redis_"


redis_settings = RedisSettings()
pprint(redis_settings)


async def request(
    method: str,
    url: str,
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Response:
    client_params = {
        "headers": {
            "user-agent": f"{app_settings.name} User-Agent",
        }
    }

    if app_settings.proxy:
        client_params.update({
            "proxies": {
                "http://": app_settings.proxy,
                "https://": app_settings.proxy,
            },
            "verify": False,
        })

    async with httpx.AsyncClient(**client_params) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json,
        )

    return response


async def request_hook(span: Span, _: Any) -> None:
    if span and span.is_recording():
        request_id = context.get(plugins.RequestIdPlugin().key.value)
        span.set_attribute("request.id", request_id)


def init_tracing():
    attributes = {"service.name": app_settings.name}
    resource = Resource.create(attributes=attributes)
    tracer = TracerProvider(resource=resource)

    jaeger_exporter = JaegerExporter(
        agent_host_name=app_settings.tracing_host,
        agent_port=app_settings.tracing_port,
    )
    span_processor = BatchSpanProcessor(jaeger_exporter)
    tracer.add_span_processor(span_processor)

    trace.set_tracer_provider(tracer)

    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument(request_hook=request_hook)


# APP
middleware = [
    Middleware(
        ContextMiddleware,
        plugins=(
            plugins.RequestIdPlugin(),
        )
    )
]

app = FastAPI(debug=True, middleware=middleware)
init_tracing()


# DEPENDENCIES
async def get_redis() -> AsyncIterator[Redis]:
    sentinel = Sentinel(
        [(redis_settings.host, redis_settings.port)],
        socket_timeout=redis_settings.timeout,
        password=redis_settings.password,
    )

    session = sentinel.master_for(
        redis_settings.sentinel_master,
        socket_timeout=redis_settings.timeout,
    )
    yield session
    await session.close()


# SCHEMAS
class ItemSchema(BaseModel):
    id: UUID
    name: str
    value: int


class ItemResponseSchema(BaseModel):
    message: str
    result: ItemSchema | list[ItemSchema] | None


# ROUTES
@app.get("/items", response_model=ItemResponseSchema)
async def list_item(redis: Redis = Depends(get_redis)) -> ItemResponseSchema:
    response = await redis.keys("item_*")
    result = [await redis.get(x) for x in response]
    result = [ItemSchema.model_validate_json(x) for x in result if x]
    return ItemResponseSchema(message="list item", result=result)


@app.post("/items", response_model=ItemResponseSchema, status_code=HTTP_201_CREATED)
async def create_item(name: str, value: int, redis: Redis = Depends(get_redis)) -> ItemResponseSchema:
    result = ItemSchema(id=uuid4(), name=name, value=value)
    await redis.set(f"item_{result.id}", result.model_dump_json())
    return ItemResponseSchema(message=f"create item", result=result)


@app.get("/items/{item_id:uuid}", response_model=ItemResponseSchema)
async def get_item(item_id: UUID, redis: Redis = Depends(get_redis)) -> ItemResponseSchema:
    response = await redis.get(f"item_{item_id}")
    result = ItemSchema.model_validate_json(response)

    response = await request("GET", app_settings.external_url)
    pprint(response)

    return ItemResponseSchema(message=f"get item {item_id}", result=result)


@app.delete("/items/{item_id:uuid}", response_model=ItemResponseSchema)
async def delete_item(item_id: UUID, redis: Redis = Depends(get_redis)):
    await redis.delete(f"item_{item_id}")
    result = None
    return ItemResponseSchema(message=f"delete item {item_id}", result=result)
