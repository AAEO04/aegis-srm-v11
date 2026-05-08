#!/bin/bash
# Launch AEGIS-SRM Streamlit UI
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)"
streamlit run aegis_ui/app.py \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
