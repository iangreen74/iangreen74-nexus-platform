"""
Dogfood catalogue — minimal apps that Overwatch can deploy on a schedule
to accumulate DeployAttempt training data. Each entry MUST ship a Dockerfile;
Forgewing does not synthesize one for these automated runs.

Start narrow (3 confirmed-buildable stacks). Expand only after the success
rate on these stays above 50% for a full day of automated runs.
"""
from __future__ import annotations

import json
from typing import Any

CATALOGUE: list[dict[str, Any]] = [
    {
        "name": "df-flask-api",
        "desc": "Flask REST API with /health and /api/hello",
        "fingerprint": "python/flask",
        "files": {
            "app.py": (
                "from flask import Flask, jsonify\n"
                "app = Flask(__name__)\n\n"
                "@app.route('/health')\n"
                "def health():\n"
                "    return jsonify({'status': 'ok'})\n\n"
                "@app.route('/api/hello')\n"
                "def hello():\n"
                "    return jsonify({'msg': 'hello'})\n\n"
                "if __name__ == '__main__':\n"
                "    app.run(host='0.0.0.0', port=8000)\n"
            ),
            "requirements.txt": "flask==3.0.0\ngunicorn==21.2.0\n",
            "Dockerfile": (
                "FROM python:3.11-slim\n"
                "WORKDIR /app\n"
                "COPY requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\n"
                "EXPOSE 8000\n"
                'CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]\n'
            ),
        },
    },
    {
        "name": "df-fastapi",
        "desc": "FastAPI with /health and /api/hello",
        "fingerprint": "python/fastapi",
        "files": {
            "main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return {'status': 'ok'}\n\n"
                "@app.get('/api/hello')\n"
                "def hello():\n"
                "    return {'msg': 'hello'}\n"
            ),
            "requirements.txt": "fastapi==0.109.0\nuvicorn[standard]==0.27.0\n",
            "Dockerfile": (
                "FROM python:3.11-slim\n"
                "WORKDIR /app\n"
                "COPY requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\n"
                "EXPOSE 8000\n"
                'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
            ),
        },
    },
    {
        "name": "df-express",
        "desc": "Express.js API with /health and /api/hello",
        "fingerprint": "node/express",
        "files": {
            "server.js": (
                "const express = require('express');\n"
                "const app = express();\n"
                "app.get('/health', (req, res) => res.json({status: 'ok'}));\n"
                "app.get('/api/hello', (req, res) => res.json({msg: 'hello'}));\n"
                "app.listen(process.env.PORT || 8000);\n"
            ),
            "package.json": json.dumps({
                "name": "df-express",
                "version": "1.0.0",
                "scripts": {"start": "node server.js"},
                "dependencies": {"express": "^4.18.2"},
            }, indent=2) + "\n",
            "Dockerfile": (
                "FROM node:18-alpine\n"
                "WORKDIR /app\n"
                "COPY package.json .\n"
                "RUN npm install --omit=dev\n"
                "COPY . .\n"
                "EXPOSE 8000\n"
                'CMD ["node", "server.js"]\n'
            ),
        },
    },
]


def pick_app(position: int) -> dict[str, Any]:
    """Return the catalogue entry at `position` (wraps around)."""
    return CATALOGUE[position % len(CATALOGUE)]
