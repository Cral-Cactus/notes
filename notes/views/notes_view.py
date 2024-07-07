import os
import re
import webbrowser
from enum import Enum
from os import path, linesep
from os.path import exists

from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import ObjectProperty, StringProperty
from kivy.uix.scrollview import ScrollView
from kivymd.theming import ThemableBehavior
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.dialog import MDDialog
from kivymd.uix.textfield import TextInput
from kivymd.uix.filemanager import MDFileManager
from kivymd.uix.list import (
    MDList,
    OneLineAvatarIconListItem,
    ThreeLineListItem,
    IRightBodyTouch,
)
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.snackbar import BaseSnackbar

from kivy.base import EventLoop
from kivy.uix.textinput import FL_IS_LINEBREAK

from notes_app import __version__
from notes_app.diff import merge_strings
from notes_app.observer.notes_observer import Observer

from notes_app.color import (
    get_color_by_name,
    get_next_color_by_rgba,
    AVAILABLE_COLORS,
    AVAILABLE_SNACK_BAR_COLORS,
)
from notes_app.file import (
    get_validated_file_path,
    File,
    transform_section_separator_to_section_name,
    transform_section_name_to_section_separator,
    SECTION_FILE_NEW_SECTION_PLACEHOLDER,
    SECTION_FILE_NAME_MINIMAL_CHAR_COUNT,
)
from notes_app.font import get_next_font, AVAILABLE_FONTS
from notes_app.mark import get_marked_text
from notes_app.search import (
    Search,
    validate_search_input,
    SEARCH_LIST_ITEM_MATCHED_EXTRA_CHAR_COUNT,
    SEARCH_LIST_ITEM_MATCHED_HIGHLIGHT_COLOR,
    SEARCH_LIST_ITEM_MATCHED_HIGHLIGHT_STYLE,
    transform_section_text_placeholder_to_section_name,
    transform_section_name_to_section_text_placeholder,
    transform_position_text_placeholder_to_position,
    transform_position_to_position_text_placeholder,
)

APP_TITLE = "Notes"
APP_METADATA_ROWS = [
    "A simple notes application",
    "built with Python 3.8 & KivyMD",
    f"version {__version__}",
]
EXTERNAL_REPOSITORY_URL = "https://github.com/Cral-Cactus/notes"


class CustomTextInput(TextInput):
    # overriding TextInput.insert_text() with added extra condition and (len(_lines_flags) - 1 >= row + 1)
    # to handle a edge case when external update adds multiple line breaks and results in uncaught index error
    def insert_text(self, substring, from_undo=False):
        """Insert new text at the current cursor position. Override this
        function in order to pre-process text for input validation.
        """
        _lines = self._lines
        _lines_flags = self._lines_flags

        if self.readonly or not substring or not self._lines:
            return

        if isinstance(substring, bytes):
            substring = substring.decode("utf8")

        if self.replace_crlf:
            substring = substring.replace("\r\n", "\n")

        self._hide_handles(EventLoop.window)

        if not from_undo and self.multiline and self.auto_indent and substring == "\n":
            substring = self._auto_indent(substring)

        mode = self.input_filter
        if mode not in (None, "int", "float"):
            substring = mode(substring, from_undo)
            if not substring:
                return

        col, row = self.cursor
        cindex = self.cursor_index()
        text = _lines[row]
        len_str = len(substring)
        new_text = text[:col] + substring + text[col:]
        if mode is not None:
            if mode == "int":
                if not re.match(self._insert_int_pat, new_text):
                    return
            elif mode == "float":
                if not re.match(self._insert_float_pat, new_text):
                    return
        self._set_line_text(row, new_text)

        if (
            len_str > 1
            or substring == "\n"
            or (substring == " " and _lines_flags[row] != FL_IS_LINEBREAK)
            or (
                row + 1 < len(_lines)
                and (len(_lines_flags) - 1 >= row + 1)
                and _lines_flags[row + 1] != FL_IS_LINEBREAK
            )
            or (
                self._get_text_width(new_text, self.tab_width, self._label_cached)
                > (self.width - self.padding[0] - self.padding[2])
            )
        ):
            # Avoid refreshing text on every keystroke.
            # Allows for faster typing of text when the amount of text in
            # TextInput gets large.

            (start, finish, lines, lines_flags, len_lines) = self._get_line_from_cursor(
                row, new_text
            )

            # calling trigger here could lead to wrong cursor positioning
            # and repeating of text when keys are added rapidly in a automated
            # fashion. From Android Keyboard for example.
            self._refresh_text_from_property(
                "insert", start, finish, lines, lines_flags, len_lines
            )

        self.cursor = self.get_cursor_from_index(cindex + len_str)
        # handle undo and redo
        self._set_unredo_insert(cindex, cindex + len_str, substring, from_undo)


class IconsContainer(IRightBodyTouch, MDBoxLayout):
    pass


class ItemDrawer(OneLineAvatarIconListItem):
    id = StringProperty(None)
    text = StringProperty(None)
    edit = ObjectProperty(None)
    delete = ObjectProperty(None)


class ContentNavigationDrawer(MDBoxLayout):
    pass


class DrawerList(ThemableBehavior, MDList):
    pass  # set_color_item causing app crashes hard to reproduce

    # def set_color_item(self, instance_item):
    #     """Called when tap on a menu item.
    #     Set the color of the icon and text for the menu item.
    #     """
    #     for item in self.children:
    #         if item.text_color == self.theme_cls.primary_color:
    #             item.text_color = self.theme_cls.text_color
    #             break
    #     instance_item.text_color = self.theme_cls.primary_color


class OpenFileDialogContent(MDBoxLayout):
    open_file = ObjectProperty(None)
    cancel = ObjectProperty(None)


class ShowFileMetadataDialogContent(MDBoxLayout):
    show_file_metadata_label = ObjectProperty(None)
    cancel = ObjectProperty(None)


class ShowAppMetadataDialogContent(MDBoxLayout):
    show_app_metadata_label = ObjectProperty(None)
    execute_goto_external_url = ObjectProperty(None)
    cancel = ObjectProperty(None)


class AddSectionDialogContent(MDBoxLayout):
    add_section_result_message = StringProperty(None)
    execute_add_section = ObjectProperty(None)
    cancel = ObjectProperty(None)


class EditSectionDialogContent(MDBoxLayout):
    old_section_name = StringProperty(None)
    edit_section_result_message = StringProperty(None)
    execute_edit_section = ObjectProperty(None)
    cancel = ObjectProperty(None)


class SearchDialogContent(MDBoxLayout):
    get_search_switch_state = ObjectProperty(None)
    search_switch_callback = ObjectProperty(None)
    search_string_placeholder = StringProperty(None)
    search_results_message = StringProperty(None)
    execute_search = ObjectProperty(None)
    cancel = ObjectProperty(None)


class ScrollableLabel(ScrollView):
    pass


class CustomListItem(ThreeLineListItem):
    pass


class CustomSnackbar(BaseSnackbar):
    text = StringProperty(None)
    icon = StringProperty(None)


class MenuStorageItems(Enum):
    ChooseFile = "Choose storage file"
    ShowFileInfo = "Show storage file info"
    Save = "Save storage file"


class MenuSettingsItems(Enum):
    SetNextFont = "Set next font"
    IncreaseFontSize = "Increase font size"
    DecreaseFontSize = "Decrease font size"
    SetNextBackgroundColor = "Set next background color"
    SetNextForegroundColor = "Set next foreground color"
    Save = "Save settings"
    ShowAppInfo = "Show application info"


class NotesView(MDBoxLayout, MDScreen, Observer):
    """"
    A class that implements the visual presentation `NotesModel`.

    """

    settings = ObjectProperty()
    defaults = ObjectProperty()

    controller = ObjectProperty()
    model = ObjectProperty()

    def __init__(self, **kw):
        super().__init__(**kw)
        self.model.add_observer(self)  # register the view as an observer

        self.menu_storage = self.get_menu_storage()
        self.menu_settings = self.get_menu_settings()
        self.snackbar = None
        self.dialog = None

        self.manager_open = False
        self.file_manager = None

        self.last_searched_string = str()
        self.auto_save_text_input_change_counter = 0

        self.search = Search(defaults=self.defaults)
        self.set_properties_from_settings()

        self.file = File(
            file_path=self.model.file_path,
            controller=self.controller,
            defaults=self.defaults,
        )
        self.current_section = self.file.default_section_separator
        self.filter_data_split_by_section()
        self.set_drawer_items(section_separators=self.file.section_separators_sorted)

    @property
    def is_unsaved_change(self):
        return self.auto_save_text_input_change_counter > 0

    def set_properties_from_settings(self):
        self.text_section_view.font_name = self.settings.font_name
        self.text_section_view.font_size = self.settings.font_size
        self.text_section_view.background_color = get_color_by_name(
            colors_list=AVAILABLE_COLORS, color_name=self.settings.background_color
        ).rgba_value
        self.text_section_view.foreground_color = get_color_by_name(
            colors_list=AVAILABLE_COLORS, color_name=self.settings.foreground_color
        ).rgba_value

    def filter_data_split_by_section(self, section_separator=None):
        section_separator = section_separator or self.current_section

        self.text_section_view.section_file_separator = section_separator

        self.text_section_view.text = self.file.get_section_content(
            section_separator=section_separator
        )

        # setting self.text_section_view.text invokes the on_text event method
        # but changing the section without any actual typing is not an unsaved change
        self.auto_save_text_input_change_counter = 0

        # de-select text to cover edge case when
        # the search result is selected even after the related section is deleted
        self.text_section_view.select_text(0, 0)

        section_name = transform_section_separator_to_section_name(
            defaults=self.defaults, section_separator=section_separator
        )

        self.ids.toolbar.title = f"{APP_TITLE} section: {section_name}"

    def set_drawer_items(self, section_separators):
        self.ids.md_list.clear_widgets()

        for section_separator in section_separators:
            self.ids.md_list.add_widget(
                ItemDrawer(
                    id=section_separator,
                    text=transform_section_separator_to_section_name(
                        defaults=self.defaults, section_separator=section_separator
                    ),
                    on_release=lambda x=f"{section_separator}": self.press_drawer_item_callback(
                        x
                    ),
                    edit=self.press_edit_section,
                    delete=self.press_delete_section,
                )
            )