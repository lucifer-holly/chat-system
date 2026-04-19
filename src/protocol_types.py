"""
Protocol-level constants: message types and error codes.

Centralising these as enums prevents typo bugs ("regiter" vs "register")
and makes refactoring safer than scattered string literals.
"""

from enum import Enum


class MsgType(str, Enum):
    """All application-layer message types.

    Inheriting from str lets us use these directly as JSON values
    without manual `.value` conversion.
    """

    # Authentication
    REGISTER = "register"
    REGISTER_RESP = "register_resp"
    LOGIN = "login"
    LOGIN_RESP = "login_resp"
    LOGOUT = "logout"
    LOGOUT_RESP = "logout_resp"

    # Group management
    CREATE_GROUP = "create_group"
    CREATE_GROUP_RESP = "create_group_resp"
    JOIN_GROUP = "join_group"
    JOIN_GROUP_RESP = "join_group_resp"
    LEAVE_GROUP = "leave_group"
    LEAVE_GROUP_RESP = "leave_group_resp"
    LIST_GROUPS = "list_groups"
    LIST_GROUPS_RESP = "list_groups_resp"

    # Messaging
    SEND_MSG = "send_msg"
    SEND_MSG_ACK = "send_msg_ack"
    RECV_MSG = "recv_msg"

    # Presence
    USER_STATUS = "user_status"

    # Connection keep-alive
    HEARTBEAT = "heartbeat"
    HEARTBEAT_RESP = "heartbeat_resp"

    # Generic error envelope
    ERROR = "error"


class ErrorCode(int, Enum):
    """Error codes returned in the `code` field of error responses."""

    USER_EXISTS = 1001
    WRONG_PASSWORD = 1002
    NOT_LOGGED_IN = 1003
    GROUP_NOT_FOUND = 1004
    ALREADY_LOGGED_IN = 1005
    INVALID_PARAMS = 1006
    UNKNOWN = 9999


# Human-readable messages for each error code.  The client may fall back to
# the server-provided `msg` field, but having a stable default is handy.
ERROR_MESSAGES = {
    ErrorCode.USER_EXISTS: "User already exists",
    ErrorCode.WRONG_PASSWORD: "Incorrect username or password",
    ErrorCode.NOT_LOGGED_IN: "Not logged in",
    ErrorCode.GROUP_NOT_FOUND: "Group not found",
    ErrorCode.ALREADY_LOGGED_IN: "User already logged in from another session",
    ErrorCode.INVALID_PARAMS: "Invalid or missing parameters",
    ErrorCode.UNKNOWN: "Unknown error",
}


class ContentType(str, Enum):
    """Supported message content formats."""

    TEXT = "text"
    IMAGE = "image"  # base64-encoded


class TargetType(str, Enum):
    """Whether a message is addressed to a group or a direct peer."""

    GROUP = "group"
    USER = "user"


class UserStatus(str, Enum):
    """User presence events broadcast to interested peers."""

    ONLINE = "online"
    OFFLINE = "offline"
    JOINED = "joined"
    LEFT = "left"
