from rest_framework.views import exception_handler

from app.utils.errors import first_error_message


def api_exception_handler(exc, context):
    """Wrap every DRF error in the documented envelope:

    {"status", "success": false, "message", "errors"}
    """
    response = exception_handler(exc, context)
    if response is None:
        return response

    data = response.data
    if isinstance(data, dict) and {"success", "status"} <= set(data):
        # Already enveloped.
        return response

    if isinstance(data, dict) and "detail" in data:
        message = data["detail"]
    else:
        message = first_error_message(data, fallback="Request failed.")

    response.data = {
        "status": response.status_code,
        "success": False,
        "message": str(message),
        "errors": data,
    }
    return response
