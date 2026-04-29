"""Ecovacs mower constants."""

DOMAIN = "ecovacs_goat_g1"
SERVICE_REFRESH_STATE = "refresh_state"
SERVICE_REQUEST_LIVE_POSITION_STREAM = "request_live_position_stream"
SERVICE_START_DEBUG_CAPTURE = "start_debug_capture"
SERVICE_STOP_DEBUG_CAPTURE = "stop_debug_capture"
SERVICE_CLEAR_DEBUG_CAPTURE = "clear_debug_capture"
SERVICE_MARK_DEBUG_CAPTURE = "mark_debug_capture"
SERVICE_EXPORT_DEBUG_CAPTURE = "export_debug_capture"

OPTION_DEBUG_CAPTURE_RAW_PAYLOADS = "debug_capture_raw_payloads"
OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES = "debug_capture_max_duration_minutes"
OPTION_DEBUG_CAPTURE_MAX_SIZE_MB = "debug_capture_max_size_mb"

DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS = False
DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES = 30
DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB = 25
