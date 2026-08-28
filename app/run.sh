#!/bin/bash
# The Lambda Web Adapter's startup command. The function's handler is a path under the
# bundle root, so this file has to be present at it and executable (git carries 100755).
#
# PYTHONPATH is not optional: this is a fresh python process, so nothing has put the deps
# layer, the bundle or the runtime's own boto3 on sys.path for it.
#
# `python -m uvicorn`, because pip --target puts the console script on no PATH. PORT is the
# stack's, read here and by the adapter, so the two cannot disagree about the traffic port.
PATH=$PATH:$LAMBDA_TASK_ROOT/bin \
    PYTHONPATH=$PYTHONPATH:/opt/python:$LAMBDA_TASK_ROOT:$LAMBDA_RUNTIME_DIR \
    exec python -m uvicorn --port=$PORT streaming_app:app
