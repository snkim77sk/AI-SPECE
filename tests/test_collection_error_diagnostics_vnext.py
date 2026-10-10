import vnext_collection
import vnext_http


def test_api_error_label_keeps_code_and_message_without_url():
    exc = vnext_http.VNextApiError("10", "INVALID_REQUEST_PARAMETER_ERROR")
    label = vnext_collection._safe_error_label(exc)
    assert label == "VNextApiError:10:INVALID_REQUEST_PARAMETER_ERROR"
    assert "http" not in label.lower()
