from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError
from PyQt5.QtCore import QDateTime, QUrl
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.api.schemas import GlucosePlotData, TableInfo, TablePage
from desktop_client.api_client import ApiClient
from desktop_client.config import DesktopSettings
from desktop_client.dialogs import RecordDialog
from desktop_client.plots import build_glucose_plot_html

USER_ROLE = 0x0100


class MainWindow(QMainWindow):
    def __init__(self, settings: DesktopSettings) -> None:
        super().__init__()
        self.setWindowTitle("Diabetes Bot: база данных")
        self.resize(1280, 720)

        self._tables: dict[str, TableInfo] = {}
        self._page: TablePage | None = None
        self._offset = 0
        self._plot_directory = TemporaryDirectory(prefix="diabetes-glucose-plot-")
        self._plot_sequence = 0
        self._client = ApiClient(
            settings.api_base_url,
            settings.admin_api_token.get_secret_value(),
            settings.api_timeout_seconds,
            self,
        )
        self._client.succeeded.connect(self._request_succeeded)
        self._client.failed.connect(self._request_failed)

        self._table_selector = QComboBox()
        self._table_selector.currentIndexChanged.connect(self._table_changed)
        self._refresh_button = QPushButton("Обновить")
        self._refresh_button.clicked.connect(self.refresh_rows)

        top = QHBoxLayout()
        top.addWidget(QLabel("Таблица:"))
        top.addWidget(self._table_selector, 1)
        top.addWidget(self._refresh_button)

        self._grid = QTableWidget()
        self._grid.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self._grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._grid.setAlternatingRowColors(True)

        self._add_button = QPushButton("Добавить")
        self._edit_button = QPushButton("Изменить")
        self._delete_button = QPushButton("Удалить")
        self._add_button.clicked.connect(self.add_row)
        self._edit_button.clicked.connect(self.edit_row)
        self._delete_button.clicked.connect(self.delete_row)

        self._previous_button = QPushButton("Назад")
        self._next_button = QPushButton("Вперёд")
        self._previous_button.clicked.connect(self.previous_page)
        self._next_button.clicked.connect(self.next_page)
        self._page_label = QLabel("0 / 0")
        self._page_size = QSpinBox()
        self._page_size.setRange(10, 500)
        self._page_size.setValue(100)
        self._page_size.valueChanged.connect(self._page_size_changed)

        bottom = QHBoxLayout()
        bottom.addWidget(self._add_button)
        bottom.addWidget(self._edit_button)
        bottom.addWidget(self._delete_button)
        bottom.addStretch(1)
        bottom.addWidget(self._previous_button)
        bottom.addWidget(self._page_label)
        bottom.addWidget(self._next_button)
        bottom.addWidget(QLabel("Строк на странице:"))
        bottom.addWidget(self._page_size)

        tables_tab = QWidget()
        tables_layout = QVBoxLayout(tables_tab)
        tables_layout.addLayout(top)
        tables_layout.addWidget(self._grid, 1)
        tables_layout.addLayout(bottom)

        current_time = QDateTime.currentDateTime()
        self._plot_start = QDateTimeEdit(current_time.addDays(-7))
        self._plot_stop = QDateTimeEdit(current_time)
        for editor in (self._plot_start, self._plot_stop):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy HH:mm")
        self._glucose_button = QPushButton("Glucose")
        self._glucose_button.clicked.connect(self.plot_glucose)

        plot_controls = QHBoxLayout()
        plot_controls.addWidget(QLabel("Start:"))
        plot_controls.addWidget(self._plot_start)
        plot_controls.addWidget(QLabel("Stop:"))
        plot_controls.addWidget(self._plot_stop)
        plot_controls.addWidget(self._glucose_button)
        plot_controls.addStretch(1)

        self._plot_view = QWebEngineView()
        self._plot_view.setHtml(
            "<html><body><p>Select an interval and click Glucose.</p></body></html>"
        )
        plots_tab = QWidget()
        plots_layout = QVBoxLayout(plots_tab)
        plots_layout.addLayout(plot_controls)
        plots_layout.addWidget(self._plot_view, 1)

        tabs = QTabWidget()
        tabs.addTab(tables_tab, "Tables")
        tabs.addTab(plots_tab, "Plots")
        self.setCentralWidget(tabs)
        status_bar = self.statusBar()
        assert status_bar is not None
        self._status_bar: QStatusBar = status_bar
        self._status_bar.showMessage("Подключение к API…")
        self._set_actions_enabled(False)
        self._client.load_tables()

    def current_table(self) -> TableInfo | None:
        name = self._table_selector.currentData()
        return self._tables.get(str(name)) if name is not None else None

    def refresh_rows(self) -> None:
        table = self.current_table()
        if table is None:
            return
        self._status_bar.showMessage(f"Загрузка {table.name}…")
        self._client.load_rows(table.name, self._offset, self._page_size.value())

    def add_row(self) -> None:
        table = self.current_table()
        if table is None:
            return
        dialog = RecordDialog(table, parent=self)
        if dialog.exec_() == RecordDialog.Accepted and dialog.payload is not None:
            self._client.create_row(table.name, dialog.payload)

    def plot_glucose(self) -> None:
        start_seconds = self._plot_start.dateTime().toSecsSinceEpoch()
        stop_seconds = self._plot_stop.dateTime().toSecsSinceEpoch()
        if start_seconds >= stop_seconds:
            QMessageBox.warning(self, "Invalid interval", "Start must be earlier than Stop.")
            return
        start = datetime.fromtimestamp(start_seconds, UTC).isoformat()
        stop = datetime.fromtimestamp(stop_seconds, UTC).isoformat()
        self._status_bar.showMessage("Loading glucose measurements…")
        self._glucose_button.setEnabled(False)
        self._client.load_glucose(start, stop)

    def edit_row(self) -> None:
        table = self.current_table()
        record = self._selected_record()
        if table is None or record is None:
            QMessageBox.information(self, "Выбор строки", "Выберите строку для изменения.")
            return
        dialog = RecordDialog(table, record, self)
        if dialog.exec_() == RecordDialog.Accepted and dialog.payload is not None:
            self._client.update_row(
                table.name,
                int(record[table.primary_key]),
                dialog.payload,
            )

    def delete_row(self) -> None:
        table = self.current_table()
        record = self._selected_record()
        if table is None or record is None:
            QMessageBox.information(self, "Выбор строки", "Выберите строку для удаления.")
            return
        row_id = int(record[table.primary_key])
        text = f"Удалить строку {table.primary_key}={row_id}?"
        if table.delete_warning:
            text = f"{text}\n\n{table.delete_warning}"
        answer = QMessageBox.warning(
            self,
            "Подтверждение удаления",
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._client.delete_row(table.name, row_id)

    def previous_page(self) -> None:
        self._offset = max(0, self._offset - self._page_size.value())
        self.refresh_rows()

    def next_page(self) -> None:
        if self._page is None:
            return
        next_offset = self._offset + self._page_size.value()
        if next_offset < self._page.total:
            self._offset = next_offset
            self.refresh_rows()

    def _page_size_changed(self) -> None:
        self._offset = 0
        self.refresh_rows()

    def _table_changed(self) -> None:
        self._offset = 0
        self.refresh_rows()

    def _request_succeeded(self, operation: str, payload: object) -> None:
        try:
            if operation == "tables":
                self._show_tables(payload)
                return
            if operation.startswith("rows:"):
                page = TablePage.model_validate(payload)
                current_table = self.current_table()
                if current_table is not None and page.table == current_table.name:
                    self._show_page(page)
                return
            if operation == "plot:glucose":
                self._show_glucose_plot(GlucosePlotData.model_validate(payload))
                return
        except (ValidationError, ValueError) as error:
            self._request_failed(operation, f"Некорректный ответ API: {error}")
            return

        self._status_bar.showMessage("Изменения сохранены.", 5000)
        self.refresh_rows()

    def _request_failed(self, operation: str, message: str) -> None:
        if operation == "plot:glucose":
            self._glucose_button.setEnabled(True)
        self._status_bar.showMessage("Ошибка запроса.", 5000)
        QMessageBox.critical(self, "Ошибка API", message)

    def _show_tables(self, payload: object) -> None:
        if not isinstance(payload, list):
            raise ValueError("API did not return a table list")
        tables = [TableInfo.model_validate(item) for item in payload]
        self._tables = {table.name: table for table in tables}
        self._table_selector.blockSignals(True)
        self._table_selector.clear()
        for table in tables:
            self._table_selector.addItem(table.title, table.name)
        self._table_selector.blockSignals(False)
        self._set_actions_enabled(bool(tables))
        self._offset = 0
        self.refresh_rows()

    def _show_page(self, page: TablePage) -> None:
        self._page = page
        table = self._tables[page.table]
        columns = [field.name for field in table.fields]
        self._grid.clear()
        self._grid.setColumnCount(len(columns))
        self._grid.setHorizontalHeaderLabels(columns)
        self._grid.setRowCount(len(page.rows))
        for row_index, record in enumerate(page.rows):
            for column_index, name in enumerate(columns):
                value = record.get(name)
                item = QTableWidgetItem("" if value is None else str(value))
                item.setData(USER_ROLE, record)
                self._grid.setItem(row_index, column_index, item)
        self._grid.resizeColumnsToContents()

        first = page.offset + 1 if page.rows else 0
        last = page.offset + len(page.rows)
        self._page_label.setText(f"{first}–{last} из {page.total}")
        self._previous_button.setEnabled(page.offset > 0)
        self._next_button.setEnabled(page.offset + page.limit < page.total)
        self._status_bar.showMessage(f"Загружено строк: {len(page.rows)}", 5000)

    def _show_glucose_plot(self, data: GlucosePlotData) -> None:
        self._plot_sequence += 1
        path = Path(self._plot_directory.name) / "glucose.html"
        path.write_text(build_glucose_plot_html(data), encoding="utf-8")
        url = QUrl.fromLocalFile(str(path))
        url.setQuery(f"version={self._plot_sequence}")
        self._plot_view.load(url)
        self._glucose_button.setEnabled(True)
        self._status_bar.showMessage(
            f"Loaded glucose measurements: {len(data.points)}",
            5000,
        )

    def _selected_record(self) -> dict[str, Any] | None:
        selection_model = self._grid.selectionModel()
        assert selection_model is not None
        selected = selection_model.selectedRows()
        if not selected:
            return None
        item = self._grid.item(selected[0].row(), 0)
        record = item.data(USER_ROLE) if item is not None else None
        return record if isinstance(record, dict) else None

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._add_button.setEnabled(enabled)
        self._edit_button.setEnabled(enabled)
        self._delete_button.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802
        self._plot_directory.cleanup()
        super().closeEvent(event)
