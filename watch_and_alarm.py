"""
ウィンブルドン試合開始アラーム。

config.json に書かれた試合（選手名のペア）をESPNの公開スコアボードAPIで監視し、
試合ステータスが「開始前(pre)」から「進行中(in)」に変わったタイミングで
PC上でアラーム音とポップアップを出す。
"""
import json
import sys
import threading
import time
import urllib.request
import winsound
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

ALARM_SOUND_CANDIDATES = [
    Path(r"C:\Windows\Media\Alarm01.wav"),
    Path(r"C:\Windows\Media\Ring01.wav"),
]

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_scoreboard(league):
    url = SCOREBOARD_URL.format(league=league)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_competition(scoreboard, players):
    """players（姓の一部）が両方とも競技者名に含まれる試合を探す"""
    wanted = [p.lower() for p in players]
    for event in scoreboard.get("events", []):
        for grouping in event.get("groupings", []):
            for comp in grouping.get("competitions", []):
                names = [
                    c.get("athlete", {}).get("displayName", "").lower()
                    for c in comp.get("competitors", [])
                ]
                if all(any(w in n for n in names) for w in wanted):
                    return comp
    return None


def ring_alarm(label):
    def _play_sound():
        sound_file = next((p for p in ALARM_SOUND_CANDIDATES if p.exists()), None)
        if sound_file:
            winsound.PlaySound(
                str(sound_file),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
            )
        else:
            # フォールバック: wavが見つからない場合はビープを繰り返す
            while not stop_flag["stop"]:
                winsound.Beep(1000, 700)
                time.sleep(0.3)

    stop_flag = {"stop": False}
    sound_thread = threading.Thread(target=_play_sound, daemon=True)
    sound_thread.start()

    root = tk.Tk()
    root.title("試合開始！")
    root.attributes("-topmost", True)
    root.configure(bg="#c0392b")
    root.geometry("520x260+400+300")

    msg = tk.Label(
        root,
        text=f"試合開始！\n\n{label}",
        font=("Yu Gothic UI", 18, "bold"),
        fg="white",
        bg="#c0392b",
        justify="center",
    )
    msg.pack(expand=True, fill="both", padx=20, pady=20)

    def stop():
        stop_flag["stop"] = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        root.destroy()

    btn = tk.Button(
        root, text="停止", font=("Yu Gothic UI", 14, "bold"), command=stop, height=2
    )
    btn.pack(pady=10)

    root.lift()
    root.after(200, lambda: root.attributes("-topmost", True))
    root.mainloop()


def test_alarm():
    print("テストアラームを鳴らします。ポップアップの「停止」ボタンで止めてください。")
    ring_alarm("【テスト】アラーム動作確認")


def run():
    config = load_config()
    state = load_state()
    poll_interval = config.get("poll_interval_sec", 60)

    print(f"監視を開始します（{poll_interval}秒間隔）。Ctrl+Cで終了。")

    while True:
        scoreboard_cache = {}
        for watch in config.get("watches", []):
            if not watch.get("enabled", True):
                continue

            label = watch["label"]
            league = watch["league"]
            players = watch["players"]
            key = f"{league}:{'-'.join(players)}"

            if state.get(key, {}).get("done"):
                continue

            if league not in scoreboard_cache:
                try:
                    scoreboard_cache[league] = fetch_scoreboard(league)
                except Exception as e:
                    print(f"[エラー] {league} のスコアボード取得に失敗: {e}")
                    scoreboard_cache[league] = None

            scoreboard = scoreboard_cache[league]
            if scoreboard is None:
                continue

            comp = find_competition(scoreboard, players)
            if comp is None:
                print(f"[{label}] 対象の試合がまだ見つかりません（スケジュール未確定の可能性）")
                continue

            current_state = comp.get("status", {}).get("type", {}).get("state")
            description = comp.get("status", {}).get("type", {}).get("description")
            prev_state = state.get(key, {}).get("last_state")

            print(f"[{label}] 状態: {description} ({current_state})")

            first_observation = key not in state

            if first_observation and current_state == "post":
                # 監視開始時点で既に終了している試合は鳴らさない
                state[key] = {"last_state": current_state, "done": True}
            elif current_state == "in" and (first_observation or prev_state != "in"):
                state[key] = {"last_state": current_state, "done": True}
                save_state(state)
                print(f"[{label}] 試合開始を検知！アラームを鳴らします。")
                threading.Thread(target=ring_alarm, args=(label,), daemon=True).start()
            else:
                state.setdefault(key, {})["last_state"] = current_state

        save_state(state)
        time.sleep(poll_interval)


if __name__ == "__main__":
    if "--test-alarm" in sys.argv:
        test_alarm()
    else:
        try:
            run()
        except KeyboardInterrupt:
            print("\n終了します。")
