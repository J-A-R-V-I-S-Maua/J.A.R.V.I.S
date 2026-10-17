"""Editor de preferências e atalhos; toda alteração é um rascunho até Salvar."""
import copy
import os
import uuid
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)
from .preferences import defaults, validate_preferences
from .shortcuts import WINDOWS_RESOURCES, validate_shortcut, validate_shortcuts
from .settings_style import STYLE, HIGH_CONTRAST
from .commands import settings_command, all_help


class SettingsWindow(QDialog):
    saved = Signal(object)

    def __init__(self, controller, parent=None, demo=False):
        super().__init__(parent)
        self.controller = controller
        self.store = controller.preferences
        self.demo = demo
        self.baseline = self.store.effective
        self.draft = copy.deepcopy(self.store.shortcuts)
        self.editing = None
        self.restoring = False
        self.setWindowTitle("J.A.R.V.I.S. — Configurações")
        self.setObjectName("JarvisSettings")
        area = self.screen().availableGeometry()
        self.setMinimumSize(min(650, area.width() - 32), min(430, area.height() - 64))
        self.resize(min(960, area.width() - 48), min(850, area.height() - 64))
        self.setStyleSheet(STYLE + (HIGH_CONTRAST if self.baseline["high_contrast"] else ""))
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("Configurações")
        title.setObjectName("settingsTitle")
        header.addWidget(title)
        header.addStretch()
        paused = QLabel("●  Comandos do menu")
        paused.setObjectName("pausedBadge")
        paused.setToolTip("Diga Hey Jarvis e depois ajuda, salvar configurações ou fechar configurações.")
        header.addWidget(paused)
        root.addLayout(header)
        intro = QLabel("Personalize seus atalhos e deixe o Jarvis do seu jeito.")
        intro.setObjectName("mutedText")
        intro.setWordWrap(True)
        root.addWidget(intro)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setAccessibleName("Seções de configurações")
        self.navigation.addItems(["Atalhos", "Voz e microfone", "Aparência", "Ajuda e diagnóstico"])
        self.navigation.item(2).setToolTip("Aparência e acessibilidade")
        self.navigation.setFixedWidth(200)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self._shortcuts_page()
        self._voice_page()
        self._appearance_page()
        self._help_page()
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.status = QLabel(self.store.error or "As mudanças só são gravadas ao clicar em Salvar.")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Resultado das configurações")
        self.status.setObjectName("settingsStatus")
        self.status.setProperty("tone", "error" if self.store.error else "note")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self._button(buttons, "Restaurar preferências", self._restore)
        buttons.addStretch()
        self._button(buttons, "Cancelar", self.reject)
        self.save_button = self._button(buttons, "Salvar", self._save)
        self.save_button.setObjectName("saveSettings")
        root.addLayout(buttons)
        controller.audio_level.connect(self.meter.setValue)
        controller.diagnostic_result.connect(self.audio_status.setText)
        controller.devices_changed.connect(self._devices_updated)
        controller.state_changed.connect(self._diagnostics)
        if hasattr(controller, "settings_command_requested"):
            controller.settings_command_requested.connect(self.handle_voice)
        self._fill_preferences(self.baseline)
        self._refresh_shortcuts()
        self._new_shortcut()
        self._diagnostics(controller.current_event)
        if not demo:
            controller.request_device_list()

    def _page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 12, 12, 18)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return layout

    def handle_voice(self, text):
        if not self.isVisible() or not self.controller.settings_open:
            return
        try:
            command = settings_command(text)
            if command is None:
                self.controller._recognition_failed("Comando de configurações não reconhecido. Diga ajuda, salvar configurações ou fechar configurações. Ouvindo novamente…")
                return
            key, value = command
            if key == "save":
                self._save()
                return
            if key == "close":
                self.reject()
                return
            if key == "section":
                self.navigation.setCurrentRow(value)
                self._voice_notify(f"Seção {self.navigation.currentItem().text()}. Alterações ainda não salvas.")
                return
            controls = dict(self.voice_controls, text_size=self.text_size, number_size=self.number_size)
            if key in ("high_contrast", "remember_position"):
                (self.contrast if key == "high_contrast" else self.remember).setChecked(value)
            else:
                widget = controls[key]
                if not widget.minimum() <= value <= widget.maximum() or (isinstance(widget, QSpinBox) and value != int(value)):
                    raise ValueError(f"Informe um valor entre {widget.minimum()} e {widget.maximum()}" + (", sem casas decimais." if isinstance(widget, QSpinBox) else "."))
                widget.setValue(int(value) if isinstance(widget, QSpinBox) else value)
            labels = dict(high_contrast="Alto contraste", remember_position="Lembrar posição", text_size="Tamanho do texto",
                          number_size="Tamanho dos números", silence_seconds="Tempo de silêncio", start_timeout="Tempo para começar a falar",
                          max_seconds="Duração máxima", detection_threshold="Limiar de ativação")
            display = ("ativado" if value else "desativado") if isinstance(value, bool) else f"{value:g}".replace(".", ",")
            self._voice_notify(f"{labels[key]}: {display} no rascunho. Diga salvar configurações para gravar ou fechar configurações para descartar.")
        except ValueError as exc:
            self._voice_notify(str(exc), "error")

    def _voice_notify(self, text, tone="note"):
        self._notify(text, tone)
        self.controller._show_text(text)

    @staticmethod
    def _button(layout, label, callback):
        button = QPushButton(label)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _shortcuts_page(self):
        layout = self._page("Seus atalhos")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar pelo nome ou frase")
        self.search.setAccessibleName("Buscar atalhos")
        self.search.textChanged.connect(self._refresh_shortcuts)
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setObjectName("shortcutList")
        self.items.setMinimumHeight(110)
        self.items.setMaximumHeight(140)
        self.items.setAccessibleName("Atalhos cadastrados")
        self.items.currentRowChanged.connect(self._edit_shortcut)
        layout.addWidget(self.items)
        self.empty_list = QLabel(self.items.viewport())
        self.empty_list.setObjectName("mutedText")
        self.empty_list.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_list.setWordWrap(True)
        self.empty_list.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.items.viewport())
        empty_layout.setContentsMargins(12, 8, 12, 8)
        empty_layout.addWidget(self.empty_list)
        self.shortcut_count = QLabel()
        self.shortcut_count.setObjectName("mutedText")
        layout.addWidget(self.shortcut_count)
        controls = QHBoxLayout()
        self._button(controls, "+ Novo atalho", self._new_shortcut).setObjectName("primaryAction")
        self.delete_button = self._button(controls, "Excluir", self._delete_shortcut)
        self.delete_button.setObjectName("deleteShortcut")
        self.toggle_button = self._button(controls, "Ativar / desativar", self._toggle_shortcut)
        layout.addLayout(controls)
        editor = QWidget()
        editor.setObjectName("editorCard")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(16, 16, 16, 16)
        editor_layout.setSpacing(12)
        self.editor_title = QLabel("Novo atalho")
        self.editor_title.setObjectName("editorTitle")
        editor_layout.addWidget(self.editor_title)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ex.: YouTube")
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText("Ex.: abrir YouTube")
        self.kind = QComboBox()
        for label, value in (("Site", "site"), ("Aplicativo", "application"), ("Pasta", "folder"), ("Recurso do Windows", "windows")):
            self.kind.addItem(label, value)
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("Ex.: youtube.com")
        self.resources = QComboBox()
        for value, label in WINDOWS_RESOURCES.items():
            self.resources.addItem(label, value)
        destination_box = QWidget()
        row = QHBoxLayout(destination_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.destination, 1)
        row.addWidget(self.resources, 1)
        self.browse = self._button(row, "Escolher…", self._browse)
        self.enabled = QCheckBox("Atalho ativado")
        form.addRow("Nome", self.name)
        form.addRow("Frase de voz", self.phrase)
        form.addRow("Ação", self.kind)
        form.addRow("Destino", destination_box)
        form.addRow(self.enabled)
        editor_layout.addLayout(form)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        buttons = QHBoxLayout()
        self._button(buttons, "Adicionar / atualizar", self._put_shortcut)
        self._button(buttons, "Testar abertura", self._test_shortcut)
        editor_layout.addLayout(buttons)
        layout.addWidget(editor)
        layout.addWidget(QLabel("Preencher exemplo:"))
        templates = QGridLayout()
        for index, key in enumerate(("YouTube", "Explorador", "Configurações")):
            button = QPushButton(key)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, key=key: self._template(key))
            templates.addWidget(button, index // 2, index % 2)
        layout.addLayout(templates)
        label = QLabel('Diga “executar atalho abrir YouTube”, ou “executar atalho” e depois a frase.\nAdicionar à lista prepara o cadastro; Salvar grava todas as mudanças.')
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()

    def _voice_page(self):
        layout = self._page("Voz e microfone")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(12)
        self.microphone = QComboBox()
        self.microphone.addItem("Padrão do sistema", None)
        self.device_error = self.controller.device_error or ""
        self.devices = list(self.controller.audio_devices)
        self.microphone_edited = False
        self.microphone.activated.connect(lambda _: setattr(self, "microphone_edited", True))
        for device in self.devices:
            self.microphone.addItem(f"{device['name']} [{device['api']}]", dict(name=device["name"], api=device["api"]))
        form.addRow("Microfone", self.microphone)
        self.voice_controls = {}
        for key, label, minimum, maximum, step in (
            ("silence_seconds", "Silêncio para encerrar (s)", 0.1, 30, 0.1),
            ("start_timeout", "Espera pela primeira fala (s)", 0.1, 30, 0.5),
            ("max_seconds", "Duração máxima (s)", 0.1, 30, 1),
            ("detection_threshold", "Limiar de hey jarvis", 0.01, 1, 0.01)):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            form.addRow(label, spin)
            self.voice_controls[key] = spin
        layout.addLayout(form)
        label = QLabel("Limiar menor facilita a ativação, mas pode causar mais chamadas acidentais.\nSalve a seleção antes de testar outro microfone.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setAccessibleName("Nível de captação do microfone")
        layout.addWidget(self.meter)
        buttons = QHBoxLayout()
        self._button(buttons, "Testar captação", lambda: self.controller.test_audio())
        self._button(buttons, "Testar transcrição", lambda: self.controller.test_audio(transcription=True))
        layout.addLayout(buttons)
        self.audio_status = QLabel(self.device_error or "O teste usa o microfone salvo; nenhuma ação será executada.")
        self.audio_status.setWordWrap(True)
        self.audio_status.setAccessibleName("Resultado do teste de áudio")
        layout.addWidget(self.audio_status)
        layout.addStretch()

    def _appearance_page(self):
        layout = self._page("Aparência e acessibilidade")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(12)
        self.text_size = QSpinBox()
        self.text_size.setRange(14, 28)
        self.number_size = QSpinBox()
        self.number_size.setRange(10, 24)
        self.contrast = QCheckBox("Alto contraste")
        self.remember = QCheckBox("Lembrar posição da speakbar")
        form.addRow("Tamanho do texto (px)", self.text_size)
        form.addRow("Tamanho dos números (px)", self.number_size)
        form.addRow(self.contrast)
        form.addRow(self.remember)
        layout.addLayout(form)
        label = QLabel("A barra permanece dentro de um monitor disponível ao reconectar ou remover telas.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()

    def _help_page(self):
        layout = self._page("Ajuda e diagnóstico")
        self.diagnostics = QLabel()
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.diagnostics)
        help_text = QTextBrowser()
        help_text.setPlainText(all_help())
        layout.addWidget(help_text)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.depth = QSpinBox()
        self.depth.setRange(0, 2147483647)
        self.limit = QSpinBox()
        self.limit.setRange(1, 10000)
        form.addRow("Profundidade de leitura", self.depth)
        form.addRow("Limite de nós visitados", self.limit)
        layout.addLayout(form)

    def _fill_preferences(self, values):
        selection = values["microphone"]
        if not self.restoring and "microphone" not in self.store.overrides and os.getenv("MICROPHONE_DEVICE_INDEX", "").strip():
            try:
                device = next(d for d in self.devices if d["index"] == int(os.environ["MICROPHONE_DEVICE_INDEX"]))
                selection = dict(name=device["name"], api=device["api"])
            except (ValueError, StopIteration):
                self.device_error = "Aguardando a lista de microfones do serviço de voz."
        index = next((i for i in range(self.microphone.count()) if self.microphone.itemData(i) == selection), -1)
        if index < 0:
            self.microphone.addItem(f"Indisponível: {selection['name']} [{selection['api']}]", selection)
            index = self.microphone.count() - 1
        self.microphone.setCurrentIndex(index)
        self.initial_microphone = selection
        for key, spin in self.voice_controls.items():
            spin.setValue(values[key])
        self.text_size.setValue(values["text_size"])
        self.number_size.setValue(values["number_size"])
        self.contrast.setChecked(values["high_contrast"])
        self.remember.setChecked(values["remember_position"])
        self.depth.setValue(values["max_depth"])
        self.limit.setValue(values["max_elements"])

    def _devices_updated(self, result):
        selection = self.microphone.currentData()
        self.devices = result["devices"]
        if (not self.microphone_edited and not self.restoring and "microphone" not in self.store.overrides
                and os.getenv("MICROPHONE_DEVICE_INDEX", "").strip()):
            try:
                device = next(d for d in self.devices if d["index"] == int(os.environ["MICROPHONE_DEVICE_INDEX"]))
                selection = dict(name=device["name"], api=device["api"])
            except (ValueError, StopIteration):
                self.audio_status.setText("O microfone configurado no ambiente não está disponível. Selecione novamente.")
        self.microphone.blockSignals(True)
        self.microphone.clear()
        self.microphone.addItem("Padrão do sistema", None)
        for device in self.devices:
            self.microphone.addItem(f"{device['name']} [{device['api']}]", dict(name=device["name"], api=device["api"]))
        index = next((i for i in range(self.microphone.count()) if self.microphone.itemData(i) == selection), -1)
        if index < 0:
            self.microphone.addItem(f"Indisponível: {selection['name']} [{selection['api']}]", selection)
            index = self.microphone.count() - 1
        self.microphone.setCurrentIndex(index)
        self.microphone.blockSignals(False)
        if not self.microphone_edited:
            self.initial_microphone = selection
        if result["error"]:
            self.audio_status.setText(result["error"])

    def _kind_changed(self):
        kind = self.kind.currentData()
        self.destination.setVisible(kind != "windows")
        self.resources.setVisible(kind == "windows")
        self.browse.setVisible(kind in ("application", "folder"))

    def _new_shortcut(self):
        self.editing = None
        self.items.setCurrentRow(-1)
        self.editor_title.setText("Novo atalho")
        self.delete_button.setEnabled(False)
        self.toggle_button.setEnabled(False)
        self.name.clear()
        self.phrase.clear()
        self.destination.clear()
        self.kind.setCurrentIndex(0)
        self.enabled.setChecked(True)
        self._kind_changed()

    def _refresh_shortcuts(self):
        self.items.blockSignals(True)
        self.items.clear()
        query = self.search.text().casefold()
        self.visible_ids = []
        for item in self.draft:
            if query in f"{item['name']} {item['phrase']}".casefold():
                self.items.addItem(f"{'●' if item['enabled'] else '○'} {item['name']} — {item['phrase']}")
                self.visible_ids.append(item["id"])
        if self.editing in self.visible_ids:
            self.items.setCurrentRow(self.visible_ids.index(self.editing))
        self.items.blockSignals(False)
        count, total = len(self.visible_ids), len(self.draft)
        self.shortcut_count.setText(f"{count} de {total} atalhos" if query else f"{total} {'atalho cadastrado' if total == 1 else 'atalhos cadastrados'}")
        self.empty_list.setText("Nenhum atalho encontrado. Tente outra busca." if query else "Seu primeiro atalho começa aqui.\nClique em Novo atalho ou escolha um exemplo abaixo.")
        self.empty_list.setVisible(count == 0)
        selected = self._selected() is not None
        self.delete_button.setEnabled(selected)
        self.toggle_button.setEnabled(selected)

    def _selected(self):
        row = self.items.currentRow()
        return self.visible_ids[row] if 0 <= row < len(self.visible_ids) else self.editing

    def _edit_shortcut(self, row):
        if row < 0:
            return
        item = next(item for item in self.draft if item["id"] == self.visible_ids[row])
        self.editing = item["id"]
        self.editor_title.setText("Editar atalho")
        self.delete_button.setEnabled(True)
        self.toggle_button.setEnabled(True)
        self.name.setText(item["name"])
        self.phrase.setText(item["phrase"])
        self.kind.setCurrentIndex(self.kind.findData(item["kind"]))
        self.destination.setText(item["destination"])
        self.resources.setCurrentIndex(max(0, self.resources.findData(item["destination"])))
        self.enabled.setChecked(item["enabled"])

    def _form_shortcut(self):
        return validate_shortcut(dict(id=self.editing or uuid.uuid4().hex, name=self.name.text(),
                                      phrase=self.phrase.text(), kind=self.kind.currentData(),
                                      destination=self.resources.currentData() if self.kind.currentData() == "windows" else self.destination.text(),
                                      enabled=self.enabled.isChecked()))

    def _put_shortcut(self):
        try:
            item = self._form_shortcut()
            draft = [existing for existing in self.draft if existing["id"] != item["id"]] + [item]
            self.draft = validate_shortcuts(draft, check_destination=False)
            self.editing = item["id"]
            self.editor_title.setText("Editar atalho")
            self._refresh_shortcuts()
            self._notify("Atalho atualizado na lista. Clique em Salvar para gravar.", "success")
        except ValueError as exc:
            self._notify(str(exc), "error")

    def _delete_shortcut(self):
        key = self._selected()
        self.draft = [item for item in self.draft if item["id"] != key]
        self._new_shortcut()
        self._refresh_shortcuts()

    def _toggle_shortcut(self):
        key = self._selected()
        for item in self.draft:
            if item["id"] == key:
                item["enabled"] = not item["enabled"]
                self.enabled.setChecked(item["enabled"])
        self._refresh_shortcuts()

    def _template(self, key):
        self._new_shortcut()
        self.name.setText(key)
        self.phrase.setText("abrir " + key)
        if key == "Configurações":
            self.name.setText("Configurações do Windows")
            self.phrase.setText("abrir configurações do Windows")
        if key == "YouTube":
            self.destination.setText("https://youtube.com")
        else:
            self.kind.setCurrentIndex(self.kind.findData("windows"))
            self.resources.setCurrentIndex(self.resources.findData("explorer" if key == "Explorador" else "settings"))

    def _browse(self):
        if self.kind.currentData() == "folder":
            path = QFileDialog.getExistingDirectory(self, "Selecionar pasta")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Selecionar aplicativo ou atalho", "", "Aplicativos (*.exe *.lnk)")
        if path:
            self.destination.setText(path)

    def _test_shortcut(self):
        try:
            self._notify(self.controller.shortcut_service.execute(self._form_shortcut()), "success")
        except ValueError as exc:
            self._notify(str(exc), "error")

    def _restore(self):
        self.restoring = True
        self._fill_preferences(defaults())
        self._notify("Preferências restauradas no rascunho; seus atalhos foram preservados. Clique em Salvar.")

    def _save(self):
        try:
            if self.name.text().strip() or self.phrase.text().strip():
                item = self._form_shortcut()
                self.draft = validate_shortcuts([existing for existing in self.draft if existing["id"] != item["id"]] + [item], check_destination=False)
                self.editing = item["id"]
            values = dict(self.baseline)
            values.update({key: spin.value() for key, spin in self.voice_controls.items()})
            values.update(microphone=self.microphone.currentData(), text_size=self.text_size.value(),
                          number_size=self.number_size.value(), high_contrast=self.contrast.isChecked(),
                          remember_position=self.remember.isChecked(), max_depth=self.depth.value(), max_elements=self.limit.value())
            validate_preferences(values)
            overrides = {} if self.restoring else dict(self.store.overrides)
            baseline = defaults() if self.restoring else self.baseline
            for key, value in values.items():
                original = self.initial_microphone if key == "microphone" else baseline[key]
                if value != original:
                    overrides[key] = value
            if self.restoring:
                overrides.pop("position", None)
            self.store.save(overrides, self.draft)
            self.controller.apply_preferences(self.baseline)
            self.saved.emit(self.store.effective)
            self.accept()
        except (ValueError, OSError) as exc:
            self._notify(f"Não foi possível salvar: {exc}", "error")
            if hasattr(self.controller, "_show_text"):
                self.controller._show_text(f"Não foi possível salvar: {exc}. Revise o rascunho ou diga fechar configurações.")

    def _notify(self, text, tone="note"):
        self.status.setText(text)
        self.status.setProperty("tone", tone)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setAccessibleDescription(text)

    def _diagnostics(self, event):
        screen = getattr(self.controller, "screen", None)
        unavailable = screen.session.unavailable if screen else "Demonstração"
        if not isinstance(unavailable, str):
            unavailable = None
        state = event.state.value
        from wakeword.events import State
        connection = "erro; verifique o último erro" if event.state is State.ERROR else (
            "preparando" if event.state is State.PREPARING else "serviço preparado")
        self.diagnostics.setText(f"Voz / microfone: {state}\nTranscrição: {connection}\nAcesso à tela: {unavailable or 'disponível'}\nÚltimo erro: {self.controller.last_error or 'nenhum'}")
