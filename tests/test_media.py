from aiogram.exceptions import TelegramBadRequest

from services.media_manager import send_event


class Msg:
    def __init__(self, mid, file_id=None):
        self.message_id = mid
        self.animation = type("A", (), {"file_id": file_id})() if file_id else None


class FakeBot:
    def __init__(self, reject_animation=False):
        self.animations, self.messages, self.reject = [], [], reject_animation

    async def send_animation(self, chat_id, animation, caption=None, reply_markup=None):
        if self.reject:
            raise TelegramBadRequest(method=None, message="wrong file identifier")
        self.animations.append((chat_id, animation, caption))
        return Msg(1, "FILE-ID-1")

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text))
        return Msg(2)


class MemStore:
    def __init__(self):
        self.data = {}

    async def get(self, event):
        return self.data.get(event)

    async def save(self, event, file_id):
        self.data[event] = file_id


async def test_missing_gif_falls_back_to_text(tmp_path):
    bot = FakeBot()
    await send_event(bot, 5, "spear", "SPEAR caption", store=MemStore(), assets_dir=tmp_path)
    assert bot.animations == [] and bot.messages == [(5, "SPEAR caption")]


async def test_gif_is_sent_and_file_id_cached(tmp_path):
    (tmp_path / "spear.gif").write_bytes(b"GIF89a")
    bot, store = FakeBot(), MemStore()
    await send_event(bot, 5, "spear", "caption", store=store, assets_dir=tmp_path)
    assert len(bot.animations) == 1 and store.data["spear"] == "FILE-ID-1"
    await send_event(bot, 5, "spear", "caption", store=store, assets_dir=tmp_path)
    assert bot.animations[1][1] == "FILE-ID-1"                     # reused file_id, no re-upload


async def test_telegram_rejection_falls_back_to_text(tmp_path):
    (tmp_path / "ddt.gif").write_bytes(b"GIF89a")
    bot = FakeBot(reject_animation=True)
    await send_event(bot, 5, "ddt", "DDT caption", store=MemStore(), assets_dir=tmp_path)
    assert bot.messages == [(5, "DDT caption")]


def test_mp4_files_are_found(tmp_path):
    from services import media_manager as media
    (tmp_path / "f5.mp4").write_bytes(b"mp4")
    assert media.asset_path("f5", tmp_path).name == "f5.mp4"
    assert "f5.gif" not in media.missing_assets(tmp_path)
    assert media.asset_path("ddt", tmp_path).name == "ddt.gif"      # nothing there: default name
