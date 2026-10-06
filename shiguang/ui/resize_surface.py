"""Draw complete size-aware frames while the native window is being resized.

Tk's child windows repaint separately.  The live task list is therefore drawn
into one Pillow image and swapped onto a single canvas after each size change.
Measured row positions preserve scroll position, and rows below the old window
become visible as soon as the window grows.
"""

from __future__ import annotations

from collections import Counter
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk

from .. import fonts, icons, strings, theme
from . import widgets
from .tk_text_bitmap import label_text_image


def _font(role: str):
    return (fonts.pil_font(role)
            or ImageFont.truetype(fonts.FALLBACK_FONT_FILE, theme.lpx(theme.font_size(role))))


def _text_width(font, value: str) -> int:
    return int(round(font.getlength(value)))


def _aa_image(control):
    """Reuse the exact PIL lettering and pill specification of an AAText widget."""
    spec = getattr(control, "_spec", None)
    if not spec or not spec[0]:
        return None
    return widgets._aa_text_image(spec[0], spec[1], spec[2], spec[3],
                                  spec[4], spec[5], spec[6], spec[7],
                                  spec[8], spec[10])


def _compact_for(card_width: int, layout: dict) -> bool:
    scale = theme.scale() or 1.0
    card_logic = card_width / scale
    if card_logic < theme.TASK_COMPACT_CARD_W:
        return True
    if card_logic > theme.TASK_EXPAND_CARD_W:
        return False
    return layout["compact"]


def _fit_task_title(task, card_width: int, layout: dict) -> str:
    """Use TaskCard's logical-pixel thresholds and text measurement."""
    scale = theme.scale() or 1.0
    card_logic = card_width / scale
    compact = _compact_for(card_width, layout)
    actions = layout["act_more"] if compact else layout["act_full"]
    reserved = (theme.CARD_PAD_X + theme.TASK_STRIPE_W + theme.TASK_CHECK + 8 + actions
                + theme.TASK_META_PAD_RIGHT)
    if layout["meta_shown"]:
        reserved += layout["meta_width"] / scale + theme.TASK_META_PAD_LEFT
    available = max(layout["title_min"], int(card_logic - reserved))
    role = "task_title_done" if task.done else "task_title"
    full = task.title or strings.UNNAMED_TITLE
    if fonts.measure(role, full) <= available:
        return full
    for keep in range(len(full), 0, -1):
        candidate = full[:keep].rstrip() + strings.TITLE_ELLIPSIS
        if fonts.measure(role, candidate) <= available:
            return candidate
    return strings.TITLE_ELLIPSIS


class ResizeSurface:
    def __init__(self, root, rect: tuple[int, int, int, int]) -> None:
        self.root = root
        left, top, right, bottom = rect
        self.source = ImageGrab.grab(bbox=rect).convert("RGB")
        self.width, self.height = self.source.size
        self.bg = theme.c("bg")
        self.border = theme.c("window_border")
        self.scroll_top = self.height
        self.scroll_bottom_inset = 0
        self.groups = []
        self.group_icons = {}
        self.group_layouts = {}
        self.task_layouts = {}
        page = getattr(root, "page", None)
        self.task_page = bool(getattr(root, "current_page", None) == "tasks"
                              and hasattr(page, "group_cards"))
        if self.task_page:
            try:
                self.scroll_top = max(0, page.scroll._parent_canvas.winfo_rooty() - top)
                scroll_canvas = page.scroll._parent_canvas
                self.scroll_bottom_inset = max(
                    0, self.height - (scroll_canvas.winfo_rooty() - top
                                      + scroll_canvas.winfo_height()))
                for group_card in page.group_cards.values():
                    group_y = group_card.header.winfo_rooty() - top
                    group_h = group_card.header.winfo_height()
                    label = group_card.icon_label
                    key = icons.resolve_key(group_card.group.icon) or "grp_work"
                    icon = icons.get_pil(key, theme.lpx(theme.GROUP_ICON))
                    if icon is not None:
                        icon_x = label.winfo_rootx() - left + (label.winfo_width() - icon.width) // 2
                        icon_y = label.winfo_rooty() - top - group_y + (label.winfo_height() - icon.height) // 2
                        self.group_icons[group_card.group.id] = (icon_x, icon_y, icon)
                    chevron = group_card.chevron
                    title_label = getattr(group_card.title_btn, "_text_label",
                                          group_card.title_btn)
                    badge = group_card.count_label
                    menu = group_card.header.winfo_children()[-1]
                    self.group_layouts[group_card.group.id] = {
                        "chevron_box": (chevron.winfo_rootx() - left,
                                        chevron.winfo_rooty() - top - group_y,
                                        chevron.winfo_width(), chevron.winfo_height()),
                        "chevron": icons.get_pil_chevron_rotated(
                            theme.GROUP_CHEVRON, group_card._chevron_angle,
                            theme.c("accent")),
                        "title_x": title_label.winfo_rootx() - left,
                        "badge": (badge.winfo_rootx() - left,
                                  badge.winfo_rooty() - top - group_y,
                                  badge.winfo_width(), badge.winfo_height()),
                        "menu_right": self.width - (menu.winfo_rootx() - left
                                                     + menu.winfo_width() // 2),
                        "menu_y": menu.winfo_rooty() - top - group_y
                                  + menu.winfo_height() // 2,
                        "menu_image": icons.get_pil("more", theme.lpx(theme.TASK_ICON)),
                    }
                    group_layout = self.group_layouts[group_card.group.id]
                    group_left_end = min(self.width, group_layout["badge"][0]
                                         + group_layout["badge"][2] + theme.lpx(5))
                    if 0 <= group_y and group_y + group_h <= self.height:
                        group_layout["left_image"] = self.source.crop(
                            (0, group_y, group_left_end, group_y + group_h))
                    rows = []
                    for card in group_card.cards:
                        card_y = card.winfo_rooty() - top
                        rows.append((card.task, card_y, card.winfo_height()))
                        due = card.due_label
                        tag = card.overdue_tag
                        check = card.check
                        title = card.title_label
                        stripe = card.stripe
                        self.task_layouts[card.task.id] = {
                            "card_width": card.winfo_width(),
                            "card_left": card.winfo_rootx() - left,
                            "card_right": self.width - (card.winfo_rootx() - left
                                                         + card.winfo_width()),
                            "check_x": check.winfo_rootx() - left
                                       + check.winfo_width() // 2,
                            "check_y": check.winfo_rooty() - top - card_y
                                       + check.winfo_height() // 2,
                            "check_image": check._pil(card.task.done, False),
                            "stripe_x": stripe.winfo_rootx() - left,
                            "stripe_y": stripe.winfo_rooty() - top - card_y,
                            "stripe_w": stripe.winfo_width(),
                            "stripe_h": stripe.winfo_height(),
                            "title_x": title.winfo_rootx() - left,
                            "title_y": title.winfo_rooty() - top - card_y
                                       + title.winfo_height() // 2,
                            "title_min": card.TITLE_MIN_WRAP,
                            "due_right": self.width - (due.winfo_rootx() - left
                                                       + due.winfo_width()),
                            "due_y": due.winfo_rooty() - top - card_y,
                            "tag_dx": tag.winfo_rootx() - due.winfo_rootx(),
                            "meta_width": card.meta_row.winfo_width(),
                            "meta_shown": bool(card.meta_row.winfo_manager()),
                            "compact": card._compact,
                            "act_full": card._act_full_w,
                            "act_more": card._act_more_w,
                            "due_image": _aa_image(due),
                            "tag_image": _aa_image(tag),
                        }
                        task_layout = self.task_layouts[card.task.id]
                        title_top = title.winfo_rooty() - top
                        title_x = task_layout["title_x"]
                        task_layout["source_title_top"] = title_top - card_y
                        native_label = getattr(title, "_label", None)
                        if native_label is not None:
                            font_actual = tkfont.Font(
                                root=root, font=native_label.cget("font")).actual()
                            task_layout["native_font"] = (
                                font_actual["family"], font_actual["size"],
                                font_actual["weight"], font_actual["slant"],
                                bool(font_actual["overstrike"]),
                                native_label.cget("fg"), native_label.cget("bg"))
                            task_layout["native_title_height"] = native_label.winfo_height()
                        role = ("task_title_done" if card.task.done
                                else "task_title")
                        shown = title.cget("text")
                        title_end = min(
                            self.width, title_x + title.winfo_width(),
                            title_x + theme.lpx(fonts.measure(role, shown) + 4))
                        if (0 <= title_top and title_top + title.winfo_height() <= self.height
                                and title_end > title_x):
                            task_layout["source_title"] = shown
                            task_layout["source_title_image"] = self.source.crop(
                                (title_x, title_top, title_end,
                                 title_top + title.winfo_height()))
                            task_layout["source_title_top"] = title_top - card_y
                    self.groups.append((group_card.group, group_y, group_h, rows))
            except Exception:  # noqa: BLE001
                self.task_page = False
                self.groups = []
        self._capture_chrome_bands(top)
        self.canvas = tk.Canvas(root, bg=self.bg, highlightthickness=0, bd=0,
                                takefocus=0)
        self.canvas.place(x=0, y=0, width=self.width, height=self.height)
        self.canvas.tk.call("raise", self.canvas._w)
        self.photo = None
        self.size = (0, 0)
        self.render(self.width, self.height)

    def _capture_chrome_bands(self, window_top: int) -> None:
        """Preserve exact chrome; expand only the empty space between its ends."""
        title_end = (self.root.titlebar.winfo_rooty()
                     + self.root.titlebar.winfo_height() - window_top)
        if self.task_page:
            summary_top = self.root.page.summary.winfo_rooty() - window_top
            banner_top = self.root.page.banner.winfo_rooty() - window_top
            end = self.scroll_top
        else:
            summary_top = title_end + theme.lpx(30)
            banner_top = summary_top + theme.lpx(46)
            end = self.height
        stops = [min(self.height, max(0, x)) for x in
                 (0, title_end, summary_top, banner_top, end)]
        title_right = self.width - (self.root.min_btn.winfo_rootx()
                                    - self.root.winfo_rootx())
        sides = [(theme.lpx(105), title_right),
                 (theme.lpx(90), theme.lpx(220)),
                 (theme.lpx(85), theme.lpx(175)),
                 (theme.lpx(215), theme.lpx(45))]
        self.bands = [(stops[i], stops[i + 1], *sides[i])
                      for i in range(4) if stops[i + 1] > stops[i]]
        colours = [Counter(self.source.crop((0, y, self.width, y + 1)).getdata())
                   .most_common(1)[0][0] for y in range(self.height)]
        self.row_background = Image.new("RGB", (1, self.height))
        self.row_background.putdata(colours)

    def _paste_chrome(self, image: Image.Image, width: int, height: int) -> None:
        for y0, original_y1, left_width, right_width in self.bands:
            y1 = min(original_y1, height)
            if y1 <= y0:
                continue
            if width == self.width:
                image.paste(self.source.crop((0, y0, width, y1)), (0, y0))
                continue
            left = min(left_width, width // 2, self.width // 2)
            right = min(right_width, width - left - 1, self.width - left - 1)
            gap = width - left - right
            image.paste(self.source.crop((0, y0, left, y1)), (0, y0))
            if gap:
                strip = self.row_background.crop((0, y0, 1, y1)).resize(
                    (gap, y1 - y0), Image.Resampling.NEAREST)
                image.paste(strip, (left, y0))
            if right:
                image.paste(self.source.crop((self.width - right, y0,
                                              self.width, y1)),
                            (width - right, y0))

    def _draw_group(self, image: Image.Image, draw: ImageDraw.ImageDraw, group, y: int,
                    row_height: int, width: int, count: int, done: int) -> None:
        cy = y + row_height // 2
        px = theme.lpx
        muted = theme.c("text_muted")
        layout = self.group_layouts[group.id]
        left_image = layout.get("left_image")
        if left_image is not None:
            image.paste(left_image, (0, y))
        else:
            arrow_x, arrow_y, arrow_w, arrow_h = layout["chevron_box"]
            if group.collapsed:
                draw.rounded_rectangle((arrow_x, y + arrow_y,
                                        arrow_x + arrow_w, y + arrow_y + arrow_h),
                                       radius=arrow_h // 2, fill=theme.c("ghost"))
            arrow = layout["chevron"]
            if arrow is not None:
                image.paste(arrow, (arrow_x + (arrow_w - arrow.width) // 2,
                                    y + arrow_y + (arrow_h - arrow.height) // 2), arrow)
            icon_data = self.group_icons.get(group.id)
            if icon_data is not None:
                icon_x, icon_y, icon = icon_data
                image.paste(icon, (icon_x, y + icon_y), icon)
            title_font = _font("group_title")
            title_x = layout["title_x"]
            name = str(group.name)
            draw.text((title_x, cy), name, font=title_font,
                      fill=theme.c("text"), anchor="lm")
            badge_x, badge_y, badge_w, badge_h = layout["badge"]
            badge_text = f"{done}/{count}"
            badge_font = _font("badge")
            draw.rounded_rectangle((badge_x, y + badge_y,
                                    badge_x + badge_w, y + badge_y + badge_h),
                                   radius=badge_h // 2, fill=theme.c("ghost"))
            draw.text((badge_x + badge_w // 2, y + badge_y + badge_h // 2), badge_text,
                      font=badge_font, fill=muted, anchor="mm")
        menu_image = layout["menu_image"]
        if menu_image is not None:
            menu_x = width - layout["menu_right"]
            menu_y = y + layout["menu_y"]
            image.paste(menu_image,
                        (menu_x - menu_image.width // 2,
                         menu_y - menu_image.height // 2), menu_image)

    def _draw_task(self, image: Image.Image, draw: ImageDraw.ImageDraw, task, y: int,
                   row_height: int, width: int) -> None:
        px = theme.lpx
        layout = self.task_layouts[task.id]
        left = layout["card_left"]
        right = width - layout["card_right"]
        # CTkFrame's drawable fill ends two physical pixels above its measured
        # window edge; drawing through the full widget height narrows every gap.
        bottom = y + row_height - 2
        cy = y + layout["check_y"]
        card_color = theme.c("card_done" if task.done else "card")
        draw.rounded_rectangle((left, y, right - 1, bottom - 1),
                               radius=px(theme.RADIUS_CARD), fill=card_color)
        if task.is_overdue:
            sx = layout["stripe_x"]
            sy = y + layout["stripe_y"]
            sw = max(1, layout["stripe_w"] - 1)
            sh = max(1, layout["stripe_h"] - 2)
            draw.rounded_rectangle((sx, sy, sx + sw - 1, sy + sh - 1),
                                   radius=min(sw // 2, max(1, px(1))),
                                   fill=theme.c("due_over"))
        check_image = layout["check_image"]
        if check_image is not None:
            image.paste(check_image,
                        (layout["check_x"] - check_image.width // 2,
                         cy - check_image.height // 2), check_image)

        card_width = right - left
        due_image = layout["due_image"]
        if due_image is not None:
            original_actions = (layout["act_more"] if layout["compact"]
                                else layout["act_full"])
            current_actions = (layout["act_more"] if _compact_for(card_width, layout)
                               else layout["act_full"])
            due_right = (width - layout["due_right"]
                         + px(original_actions - current_actions))
            due_x = due_right - due_image.width
            image.paste(due_image, (due_x, y + layout["due_y"]), due_image)
            tag_image = layout["tag_image"]
            if tag_image is not None:
                image.paste(tag_image, (due_x + layout["tag_dx"],
                                        y + layout["due_y"]), tag_image)

        role = "task_title_done" if task.done else "task_title"
        title_font = _font(role)
        title_x = layout["title_x"]
        title_y = y + layout["title_y"]
        title = _fit_task_title(task, card_width, layout)
        source_title = layout.get("source_title_image")
        if source_title is not None and title == layout.get("source_title"):
            image.paste(source_title, (title_x, y + layout["source_title_top"]))
        elif "native_font" in layout:
            family, font_size, weight, slant, strike, foreground, background = (
                layout["native_font"])
            text_width = max(1, px(fonts.measure(role, title) + 6))
            native_image = label_text_image(
                title, text_width, layout["native_title_height"], family,
                font_size, weight, slant, strike, foreground, background)
            if native_image is not None:
                image.paste(native_image,
                            (title_x, y + layout["source_title_top"]))
            else:
                draw.text((title_x, title_y), title, font=title_font,
                          fill=theme.c("task_done_text" if task.done else "text"),
                          anchor="lm")
        else:
            draw.text((title_x, title_y), title, font=title_font,
                      fill=theme.c("task_done_text" if task.done else "text"), anchor="lm")
            if task.done and title:
                draw.line((title_x, title_y, title_x + _text_width(title_font, title), title_y),
                          fill=theme.c("task_done_text"), width=max(1, px(1)))

    def _draw_tasks(self, image: Image.Image, width: int, height: int) -> None:
        if height <= self.scroll_top:
            return
        viewport_bottom = max(self.scroll_top,
                              height - self.scroll_bottom_inset)
        viewport = Image.new("RGB", (width, viewport_bottom - self.scroll_top), self.bg)
        draw = ImageDraw.Draw(viewport)
        visible_h = viewport.height
        for group, group_y, group_h, rows in self.groups:
            offset_y = group_y - self.scroll_top
            done = sum(1 for task, _y, _h in rows if task.done)
            if -group_h < offset_y < visible_h:
                self._draw_group(viewport, draw, group, offset_y, group_h,
                                 width, len(rows), done)
            if group.collapsed:
                continue
            if not rows and -group_h < offset_y < visible_h:
                draw.text((theme.lpx(25), offset_y + group_h + theme.lpx(7)),
                          strings.EMPTY_GROUP_HINT, font=_font("empty"),
                          fill=theme.c("text_done"), anchor="lt")
            for task, row_y, row_h in rows:
                row_top = row_y - self.scroll_top
                if row_top >= visible_h:
                    break
                if row_top + row_h > 0:
                    self._draw_task(viewport, draw, task, row_top, row_h, width)
        image.paste(viewport, (0, self.scroll_top))

    def render(self, width: int, height: int) -> None:
        width, height = max(1, int(width)), max(1, int(height))
        if (width, height) == self.size:
            return
        self.size = (width, height)
        self.canvas.place_configure(width=width, height=height)
        if (width, height) == (self.width, self.height):
            image = self.source.copy()
        else:
            image = Image.new("RGB", (width, height), self.bg)
            self._paste_chrome(image, width, height)
            if self.task_page:
                self._draw_tasks(image, width, height)
            else:
                copy_w, copy_h = min(width, self.width), min(height, self.height)
                image.paste(self.source.crop((0, 0, copy_w, copy_h)), (0, 0))
        ImageDraw.Draw(image).rectangle((0, 0, width - 1, height - 1),
                                        outline=self.border,
                                        width=max(1, theme.lpx(1)))
        old_photo = self.photo
        self.photo = ImageTk.PhotoImage(image, master=self.canvas)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        del old_photo

    def destroy(self) -> None:
        self.canvas.destroy()
        self.photo = None
