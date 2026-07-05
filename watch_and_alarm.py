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
from datetime import datetime, timezone
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

# 前の試合が「終了(post)」と連続で何回観測されたらアラームを鳴らすか。
# 1回だけの判定だと、データ側の一時的な乱れ（後から In Progress に戻る等）を
# 拾って試合途中で誤って鳴ってしまうことがあるため、連続確認で誤検知を防ぐ。
PREVIOUS_MATCH_CONFIRMATIONS = 2


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
    """players（姓の一部）が両方とも競技者名に含まれる試合を探す。

    同じ2選手は大会中に複数回対戦する可能性がある（前の大会・別ラウンド等）ため、
    候補が複数見つかった場合は現在時刻に最も近い（＝今まさに行われている）試合を選ぶ。
    """
    wanted = [p.lower() for p in players]
    candidates = []
    for event in scoreboard.get("events", []):
        for grouping in event.get("groupings", []):
            for comp in grouping.get("competitions", []):
                names = [
                    c.get("athlete", {}).get("displayName", "").lower()
                    for c in comp.get("competitors", [])
                ]
                if all(any(w in n for n in names) for w in wanted):
                    candidates.append(comp)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    now = datetime.now(timezone.utc)

    def time_distance(comp):
        date_str = comp.get("date")
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return float("inf")
        return abs((dt - now).total_seconds())

    return min(candidates, key=time_distance)


def get_state(comp):
    return comp.get("status", {}).get("type", {}).get("state")


def get_description(comp):
    return comp.get("status", {}).get("type", {}).get("description")


def get_scoreboard(scoreboard_cache, league):
    if league not in scoreboard_cache:
        try:
            scoreboard_cache[league] = fetch_scoreboard(league)
        except Exception as e:
            print(f"[エラー] {league} のスコアボード取得に失敗: {e}")
            scoreboard_cache[league] = None
    return scoreboard_cache[league]


def ring_alarm(label, headline="試合開始！"):
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
    root.title(headline)
    root.attributes("-topmost", True)
    root.configure(bg="#c0392b")
    root.geometry("520x260+400+300")

    msg = tk.Label(
        root,
        text=f"{headline}\n\n{label}",
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
            previous_match = watch.get("previous_match")
            key = f"{league}:{'-'.join(players)}"

            if state.get(key, {}).get("done"):
                continue

            scoreboard = get_scoreboard(scoreboard_cache, league)
            if scoreboard is None:
                continue

            comp = find_competition(scoreboard, players)
            if comp is None:
                print(f"[{label}] 対象の試合がまだ見つかりません（スケジュール未確定の可能性）")
                continue

            current_state = get_state(comp)
            description = get_description(comp)
            prev_state = state.get(key, {}).get("last_state")
            first_observation = key not in state

            print(f"[{label}] 状態: {description} ({current_state})")

            if first_observation and current_state == "post":
                # 監視開始時点で既に終了している試合は鳴らさない
                state[key] = {"last_state": current_state, "done": True}
            elif current_state == "in" and (first_observation or prev_state != "in"):
                # 対象の試合自体が開始済み（前の試合の情報が無い/取れない場合のフォールバック）
                state[key] = {"last_state": current_state, "done": True}
                save_state(state)
                print(f"[{label}] 試合開始を検知！アラームを鳴らします。")
                threading.Thread(
                    target=ring_alarm, args=(label, "試合開始！"), daemon=True
                ).start()
            elif current_state == "pre" and previous_match:
                prev_league = previous_match.get("league", league)
                prev_scoreboard = get_scoreboard(scoreboard_cache, prev_league)
                prev_comp = (
                    find_competition(prev_scoreboard, previous_match["players"])
                    if prev_scoreboard
                    else None
                )
                if prev_comp is None:
                    print(f"[{label}] （前の試合がまだ見つかりません）")
                    entry = state.setdefault(key, {})
                    entry["last_state"] = current_state
                    entry["prev_confirm_count"] = 0
                else:
                    prev_state_value = get_state(prev_comp)
                    prev_description = get_description(prev_comp)
                    print(f"  └ 前の試合の状態: {prev_description} ({prev_state_value})")
                    if prev_state_value == "post":
                        confirm_count = (
                            state.get(key, {}).get("prev_confirm_count", 0) + 1
                        )
                        if confirm_count >= PREVIOUS_MATCH_CONFIRMATIONS:
                            state[key] = {"last_state": current_state, "done": True}
                            save_state(state)
                            print(f"[{label}] 前の試合が終了！まもなく開始します。アラームを鳴らします。")
                            threading.Thread(
                                target=ring_alarm,
                                args=(label, "まもなく試合開始！（前の試合が終了しました）"),
                                daemon=True,
                            ).start()
                        else:
                            print(
                                f"  └ 前の試合の終了を確認中"
                                f"（{confirm_count}/{PREVIOUS_MATCH_CONFIRMATIONS}回連続）"
                            )
                            entry = state.setdefault(key, {})
                            entry["last_state"] = current_state
                            entry["prev_confirm_count"] = confirm_count
                    else:
                        entry = state.setdefault(key, {})
                        entry["last_state"] = current_state
                        entry["prev_confirm_count"] = 0
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
