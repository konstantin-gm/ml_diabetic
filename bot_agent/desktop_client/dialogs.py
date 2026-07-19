from __future__ import annotations

from typing import Any

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.api.schemas import TableField, TableInfo


class RecordDialog(QDialog):
    def __init__(
        self,
        table: TableInfo,
        record: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._record = record
        self._fields: dict[str, TableField] = {}
        self._editors: dict[str, QWidget] = {}
        self.payload: dict[str, Any] | None = None

        action = "Изменить" if record is not None else "Добавить"
        self.setWindowTitle(f"{action}: {table.title}")
        self.setMinimumWidth(480)

        form = QFormLayout()
        for field in table.fields:
            if record is None and field.read_only:
                continue
            if record is not None and not field.editable:
                continue
            editor = self._create_editor(field, record)
            self._fields[field.name] = field
            self._editors[field.name] = editor
            suffix = " *" if record is None and field.required else ""
            form.addRow(f"{field.name}{suffix}", editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_payload)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _create_editor(field: TableField, record: dict[str, Any] | None) -> QWidget:
        value = record.get(field.name) if record is not None else None
        if field.type == "boolean":
            editor = QComboBox()
            editor.addItem("false", False)
            editor.addItem("true", True)
            editor.setCurrentIndex(1 if value is True else 0)
            return editor

        editor = QLineEdit()
        if value is not None:
            editor.setText(str(value))
        if field.type == "datetime":
            editor.setPlaceholderText("2026-07-19T12:30:00+03:00")
        elif field.type == "decimal":
            editor.setPlaceholderText("0.00")
        return editor

    def _accept_payload(self) -> None:
        try:
            payload = self._build_payload()
        except ValueError as error:
            QMessageBox.warning(self, "Неверное значение", str(error))
            return
        if self._record is not None and not payload:
            QMessageBox.information(self, "Нет изменений", "Ни одно поле не изменено.")
            return
        self.payload = payload
        self.accept()

    def _build_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name, field in self._fields.items():
            editor = self._editors[name]
            if isinstance(editor, QComboBox):
                value: Any = bool(editor.currentData())
            else:
                assert isinstance(editor, QLineEdit)
                text = editor.text().strip()
                if not text:
                    if self._record is None:
                        if field.required:
                            raise ValueError(f"Поле {name} обязательно.")
                        continue
                    if self._record.get(name) is None:
                        continue
                    if not field.nullable:
                        raise ValueError(f"Поле {name} не может быть пустым.")
                    value = None
                else:
                    value = self._parse_text(field, text)

            if self._record is None or value != self._record.get(name):
                payload[name] = value
        return payload

    @staticmethod
    def _parse_text(field: TableField, text: str) -> Any:
        if field.type == "integer":
            try:
                return int(text)
            except ValueError as error:
                raise ValueError(f"Поле {field.name} должно быть целым числом.") from error
        if field.type == "decimal":
            normalized = text.replace(",", ".")
            try:
                float(normalized)
            except ValueError as error:
                raise ValueError(f"Поле {field.name} должно быть числом.") from error
            return normalized
        return text
