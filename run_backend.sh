#!/bin/bash

source backend/venv/bin/activate
uvicorn main:app --reload --app-dir backend