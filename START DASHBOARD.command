#!/bin/bash
# One-click start for Streamlit Dashboard
cd "$(dirname "$0")"
source venv/bin/activate
streamlit run dashboard/app.py