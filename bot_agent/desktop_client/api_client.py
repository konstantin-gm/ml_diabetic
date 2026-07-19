from __future__ import annotations

import json
from typing import Any

from PyQt5.QtCore import QByteArray, QObject, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class ApiClient(QObject):
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._token = token
        self._timeout_ms = timeout_seconds * 1000
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._handle_finished)

    def load_tables(self) -> None:
        self._request("tables", "GET", "/api/v1/tables")

    def load_rows(self, table: str, offset: int, limit: int) -> None:
        self._request(
            f"rows:{table}",
            "GET",
            f"/api/v1/tables/{table}/rows?offset={offset}&limit={limit}",
        )

    def create_row(self, table: str, payload: dict[str, Any]) -> None:
        self._request(f"create:{table}", "POST", f"/api/v1/tables/{table}/rows", payload)

    def update_row(self, table: str, row_id: int, payload: dict[str, Any]) -> None:
        self._request(
            f"update:{table}",
            "PATCH",
            f"/api/v1/tables/{table}/rows/{row_id}",
            payload,
        )

    def delete_row(self, table: str, row_id: int) -> None:
        self._request(
            f"delete:{table}",
            "DELETE",
            f"/api/v1/tables/{table}/rows/{row_id}",
        )

    def _request(
        self,
        operation: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        request = QNetworkRequest(QUrl(f"{self._base_url}{path}"))
        request.setRawHeader(b"Authorization", f"Bearer {self._token}".encode())
        request.setRawHeader(b"Accept", b"application/json")
        request.setTransferTimeout(self._timeout_ms)

        body = QByteArray()
        if payload is not None:
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            body = QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        if method == "GET":
            reply = self._manager.get(request)
        else:
            reply = self._manager.sendCustomRequest(request, method.encode("ascii"), body)
        assert reply is not None
        reply.setProperty("operation", operation)

    def _handle_finished(self, reply: QNetworkReply) -> None:
        operation = str(reply.property("operation"))
        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        raw_body = bytes(reply.readAll())
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None

        if status_code is not None and 200 <= int(status_code) < 300:
            self.succeeded.emit(operation, payload)
        else:
            self.failed.emit(operation, self._error_message(reply, status_code, payload))
        reply.deleteLater()

    @staticmethod
    def _error_message(
        reply: QNetworkReply,
        status_code: object,
        payload: object,
    ) -> str:
        if isinstance(payload, dict) and "detail" in payload:
            detail = payload["detail"]
            if isinstance(detail, list):
                return "; ".join(
                    str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                    for item in detail
                )
            return str(detail)
        if status_code is not None:
            return f"HTTP {status_code}: {reply.errorString()}"
        return reply.errorString()
