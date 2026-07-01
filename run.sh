#!/bin/bash
cd "$(dirname "$0")"
echo "SAP ETL Tool starting at http://localhost:5002"
python3 dashboard/app.py
