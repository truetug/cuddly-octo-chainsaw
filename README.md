# cuddly-octo-chainsaw
Sample project that combines fastapi 0.100 with pydantic 2, pydantic-settings, jaeger tracing, httpx client with proxy support, async redis sentinel.

## How to try it

Instructions for MacOS

    asdf local python 3.10.7
    python -m vevn venv
    source venv/bin/activate
    pip install -r requirements.txt
    python -m uvicorn app.main:app --reload

Settings are stored in local.env