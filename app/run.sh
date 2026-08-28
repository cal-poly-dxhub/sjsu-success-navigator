#!/bin/bash
# The Lambda Web Adapter's startup command, and the ONLY reason this repo has a shell
# script inside a Python package.
#
# HOW LAMBDA GETS HERE. The function sets AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap, which
# the LWA layer ships, and that wrapper is two lines:
#
#     exec -- "${LAMBDA_TASK_ROOT}/${_HANDLER}"
#
# So the function's "handler" is not a dotted module path here - it is a PATH under the
# bundle root, and this file has to be present at that path AND executable. The mode is
# carried by git (100755) and preserved by CDK asset staging into cdk.out and by the CDK
# CLI's zip, which writes st_mode into the zip entry's external attributes. Drop the
# execute bit and the deploy is clean and every invocation is a Permission denied.
#
# PYTHONPATH IS NOT OPTIONAL. This is a fresh `python` process, not the managed runtime's
# own bootstrap, so nothing has put the layer or the bundle on sys.path for it:
#   /opt/python           - where the deps layer installs (fastapi, uvicorn, pydantic)
#   $LAMBDA_TASK_ROOT     - where streaming_app.py sits. `python -m` prepends the working
#                           directory too, but naming it is a line rather than a
#                           dependency on Lambda's choice of cwd.
#   $LAMBDA_RUNTIME_DIR   - /var/runtime, where the runtime's own boto3 lives.
#
# `python -m uvicorn` rather than the `uvicorn` console script: pip --target puts that
# script at /opt/python/bin, which is on no PATH. $LAMBDA_TASK_ROOT/bin is added anyway,
# for a bundle that ever ships one.
#
# PORT is set by the stack and read TWICE - once here by uvicorn, once by the adapter,
# which uses it as the traffic port it forwards to. One variable so the two cannot
# disagree about which port the app is on.
PATH=$PATH:$LAMBDA_TASK_ROOT/bin \
    PYTHONPATH=$PYTHONPATH:/opt/python:$LAMBDA_TASK_ROOT:$LAMBDA_RUNTIME_DIR \
    exec python -m uvicorn --port=$PORT streaming_app:app
