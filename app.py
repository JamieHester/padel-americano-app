from flask import Flask, render_template, request, redirect, url_for, session
import random
from copy import deepcopy
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")


def shuffle_players(players):
    players = players[:]
    random.shuffle(players)
    return players


def reset_tournament_state():
    session["status"] = "setup"
    session["players"] = []
    session["tournament_players"] = []
    session["round_index"] = 0
    session["current_matches"] = []
    session["completed_rounds"] = []
    session["avoid_partners"] = []


def add_count(store, a, b):
    key = tuple(sorted((a, b)))
    store[key] = store.get(key, 0) + 1


def pair_count(store, a, b):
    return store.get(tuple(sorted((a, b))), 0)


def is_avoided_pair(a, b, avoid_partners):
    return sorted([a, b]) in [sorted(pair) for pair in avoid_partners]


def build_history(completed_rounds):
    partner_counts = {}
    opponent_counts = {}

    for round_data in completed_rounds:
        for match in round_data["matches"]:
            a1, a2 = match["teamA"]
            b1, b2 = match["teamB"]

            add_count(partner_counts, a1, a2)
            add_count(partner_counts, b1, b2)

            for left in match["teamA"]:
                for right in match["teamB"]:
                    add_count(opponent_counts, left, right)

    return partner_counts, opponent_counts


def score_matchup(team_a, team_b, partner_counts, opponent_counts, avoid_partners):
    score = 0

    if is_avoided_pair(team_a[0], team_a[1], avoid_partners):
        score += 10000

    if is_avoided_pair(team_b[0], team_b[1], avoid_partners):
        score += 10000

    score += pair_count(partner_counts, team_a[0], team_a[1]) * 100
    score += pair_count(partner_counts, team_b[0], team_b[1]) * 100

    for left in team_a:
        for right in team_b:
            score += pair_count(opponent_counts, left, right) * 15

    return score


def best_match_for_group(group, partner_counts, opponent_counts, avoid_partners):
    options = [
        ([group[0], group[1]], [group[2], group[3]]),
        ([group[0], group[2]], [group[1], group[3]]),
        ([group[0], group[3]], [group[1], group[2]]),
    ]

    best = None
    best_score = None

    for team_a, team_b in options:
        score = score_matchup(team_a, team_b, partner_counts, opponent_counts, avoid_partners)
        if best_score is None or score < best_score:
            best_score = score
            best = {
                "teamA": team_a,
                "teamB": team_b,
                "scoreA": "",
                "scoreB": "",
            }

    return best


def build_next_round(players, completed_rounds, tries=300):
    if len(players) < 4 or len(players) % 4 != 0:
        return []

    partner_counts, opponent_counts = build_history(completed_rounds)
    avoid_partners = session.get("avoid_partners", [])

    best_round = None
    best_round_score = None

    for _ in range(tries):
        shuffled = players[:]
        random.shuffle(shuffled)

        matches = []
        total_score = 0

        for i in range(0, len(shuffled), 4):
            group = shuffled[i:i + 4]
            chosen = best_match_for_group(group, partner_counts, opponent_counts, avoid_partners)

            matches.append({
                "id": f"round-{len(completed_rounds)+1}-{i//4}",
                "teamA": chosen["teamA"],
                "teamB": chosen["teamB"],
                "scoreA": "",
                "scoreB": "",
            })

            total_score += score_matchup(
                chosen["teamA"],
                chosen["teamB"],
                partner_counts,
                opponent_counts,
                avoid_partners,
            )

        if best_round_score is None or total_score < best_round_score:
            best_round_score = total_score
            best_round = matches

        if best_round_score == 0:
            break

    return best_round or []


def build_standings(players, completed_rounds):
    table = {
        player: {
            "name": player,
            "points": 0,
            "games_won": 0,
            "games_lost": 0,
            "wins": 0,
            "losses": 0,
            "played": 0,
        }
        for player in players
    }

    for round_data in completed_rounds:
        for match in round_data["matches"]:
            if match["scoreA"] == "" or match["scoreB"] == "":
                continue

            score_a = int(match["scoreA"])
            score_b = int(match["scoreB"])

            team_a_win = score_a > score_b
            team_b_win = score_b > score_a

            for player in match["teamA"]:
                row = table[player]
                row["points"] += score_a
                row["games_won"] += score_a
                row["games_lost"] += score_b
                row["played"] += 1
                if team_a_win:
                    row["wins"] += 1
                elif team_b_win:
                    row["losses"] += 1

            for player in match["teamB"]:
                row = table[player]
                row["points"] += score_b
                row["games_won"] += score_b
                row["games_lost"] += score_a
                row["played"] += 1
                if team_b_win:
                    row["wins"] += 1
                elif team_a_win:
                    row["losses"] += 1

    standings = list(table.values())

    standings.sort(
        key=lambda row: (
            -row["points"],
            -(row["games_won"] - row["games_lost"]),
            -row["wins"],
            row["name"].lower(),
        )
    )

    max_points = max([row["points"] for row in standings], default=1)

    for row in standings:
        row["rating"] = round((row["points"] / max_points) * 10, 1) if max_points > 0 else 0
        row["diff"] = row["games_won"] - row["games_lost"]
        row["win_rate"] = round((row["wins"] / row["played"]) * 100, 0) if row["played"] > 0 else 0

    return standings


def build_player_stats(players, completed_rounds):
    stats = {
        player: {
            "name": player,
            "played": 0,
            "points": 0,
            "against": 0,
            "wins": 0,
            "losses": 0,
            "diff": 0,
            "partners": {},
            "opponents": {},
            "matches": [],
            "best_win": None,
            "closest_match": None,
        }
        for player in players
    }

    for round_data in completed_rounds:
        round_number = round_data["round_number"]

        for match in round_data["matches"]:
            if match["scoreA"] == "" or match["scoreB"] == "":
                continue

            score_a = int(match["scoreA"])
            score_b = int(match["scoreB"])

            teams = [
                (match["teamA"], match["teamB"], score_a, score_b),
                (match["teamB"], match["teamA"], score_b, score_a),
            ]

            for team, opponents, own_score, opp_score in teams:
                won = own_score > opp_score
                lost = opp_score > own_score
                margin = own_score - opp_score

                for player in team:
                    teammate = team[0] if team[1] == player else team[1]
                    row = stats[player]

                    row["played"] += 1
                    row["points"] += own_score
                    row["against"] += opp_score
                    row["diff"] += margin

                    if won:
                        row["wins"] += 1
                    elif lost:
                        row["losses"] += 1

                    row["partners"][teammate] = row["partners"].get(teammate, 0) + 1

                    for opponent in opponents:
                        row["opponents"][opponent] = row["opponents"].get(opponent, 0) + 1

                    match_log = {
                        "round": round_number,
                        "partner": teammate,
                        "opponents": f"{opponents[0]} / {opponents[1]}",
                        "score": f"{own_score}-{opp_score}",
                        "result": "W" if won else "L" if lost else "D",
                        "margin": margin,
                    }
                    row["matches"].append(match_log)

                    if won and (row["best_win"] is None or margin > row["best_win"]["margin"]):
                        row["best_win"] = match_log

                    if row["closest_match"] is None or abs(margin) < abs(row["closest_match"]["margin"]):
                        row["closest_match"] = match_log

    output = []

    for player, row in stats.items():
        row["win_rate"] = round((row["wins"] / row["played"]) * 100, 0) if row["played"] else 0

        row["top_partner"] = "—"
        if row["partners"]:
            row["top_partner"] = sorted(row["partners"].items(), key=lambda x: (-x[1], x[0]))[0][0]

        row["most_faced"] = "—"
        if row["opponents"]:
            row["most_faced"] = sorted(row["opponents"].items(), key=lambda x: (-x[1], x[0]))[0][0]

        output.append(row)

    output.sort(key=lambda row: (-row["points"], -row["diff"], row["name"].lower()))
    return output


def apply_scores_from_form(matches, form_data):
    updated = deepcopy(matches)
    for i, match in enumerate(updated):
        score_a = form_data.get(f"scoreA_{i}", "").strip()
        score_b = form_data.get(f"scoreB_{i}", "").strip()
        match["scoreA"] = score_a if score_a.isdigit() else ""
        match["scoreB"] = score_b if score_b.isdigit() else ""
    return updated


@app.route("/")
def index():
    if "status" not in session:
        reset_tournament_state()

    players = session.get("players", [])
    tournament_players = session.get("tournament_players", [])
    completed_rounds = session.get("completed_rounds", [])
    standings = build_standings(tournament_players, completed_rounds) if tournament_players else []

    return render_template(
        "index.html",
        status=session.get("status", "setup"),
        players=players,
        tournament_players=tournament_players,
        round_index=session.get("round_index", 0),
        current_matches=session.get("current_matches", []),
        completed_rounds=completed_rounds,
        standings=standings,
        avoid_partners=session.get("avoid_partners", []),
        player_count_valid=(len(players) >= 4 and len(players) % 4 == 0),
    )


@app.route("/results")
def results():
    tournament_players = session.get("tournament_players", [])
    completed_rounds = session.get("completed_rounds", [])
    standings = build_standings(tournament_players, completed_rounds) if tournament_players else []

    return render_template(
        "results.html",
        tournament_players=tournament_players,
        completed_rounds=completed_rounds,
        standings=standings,
    )


@app.route("/stats")
def stats():
    tournament_players = session.get("tournament_players", [])
    completed_rounds = session.get("completed_rounds", [])
    player_stats = build_player_stats(tournament_players, completed_rounds) if tournament_players else []

    return render_template(
        "stats.html",
        tournament_players=tournament_players,
        completed_rounds=completed_rounds,
        player_stats=player_stats,
    )


@app.route("/add-player", methods=["POST"])
def add_player():
    name = request.form.get("name", "").strip()
    players = session.get("players", [])

    if name and name not in players:
        players.append(name)
        session["players"] = players

    return redirect(url_for("index"))


@app.route("/remove-player", methods=["POST"])
def remove_player():
    name = request.form.get("name", "")
    players = session.get("players", [])

    session["players"] = [p for p in players if p != name]
    session["avoid_partners"] = [
        pair for pair in session.get("avoid_partners", [])
        if name not in pair
    ]

    return redirect(url_for("index"))


@app.route("/add-avoid", methods=["POST"])
def add_avoid():
    p1 = request.form.get("player1")
    p2 = request.form.get("player2")

    if p1 and p2 and p1 != p2:
        avoid = session.get("avoid_partners", [])
        pair = sorted([p1, p2])

        if pair not in [sorted(p) for p in avoid]:
            avoid.append(pair)

        session["avoid_partners"] = avoid

    return redirect(url_for("index"))


@app.route("/remove-avoid", methods=["POST"])
def remove_avoid():
    p1 = request.form.get("player1")
    p2 = request.form.get("player2")
    pair = sorted([p1, p2])

    session["avoid_partners"] = [
        p for p in session.get("avoid_partners", [])
        if sorted(p) != pair
    ]

    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
def start():
    players = session.get("players", [])

    if len(players) < 4 or len(players) % 4 != 0:
        return redirect(url_for("index"))

    shuffled = shuffle_players(players)

    session["tournament_players"] = shuffled
    session["round_index"] = 0
    session["completed_rounds"] = []
    session["current_matches"] = build_next_round(shuffled, [])
    session["status"] = "active"

    return redirect(url_for("index"))


@app.route("/save-round", methods=["POST"])
def save_round():
    if session.get("status") != "active":
        return redirect(url_for("index"))

    current_matches = apply_scores_from_form(session.get("current_matches", []), request.form)

    if not current_matches or any(m["scoreA"] == "" or m["scoreB"] == "" for m in current_matches):
        session["current_matches"] = current_matches
        return redirect(url_for("index"))

    completed_rounds = session.get("completed_rounds", [])
    round_index = session.get("round_index", 0)

    completed_rounds.append({
        "round_number": round_index + 1,
        "matches": current_matches,
    })

    tournament_players = session.get("tournament_players", [])
    next_round_index = round_index + 1

    session["completed_rounds"] = completed_rounds
    session["round_index"] = next_round_index
    session["current_matches"] = build_next_round(tournament_players, completed_rounds)

    return redirect(url_for("index"))


@app.route("/end-now", methods=["POST"])
def end_now():
    if session.get("status") != "active":
        return redirect(url_for("index"))

    current_matches = apply_scores_from_form(session.get("current_matches", []), request.form)

    if current_matches and all(m["scoreA"] != "" and m["scoreB"] != "" for m in current_matches):
        completed_rounds = session.get("completed_rounds", [])
        completed_rounds.append({
            "round_number": session.get("round_index", 0) + 1,
            "matches": current_matches,
        })
        session["completed_rounds"] = completed_rounds

    session["current_matches"] = []
    session["status"] = "ended"

    return redirect(url_for("index"))


@app.route("/edit-round/<int:round_number>")
def edit_round(round_number):
    completed_rounds = session.get("completed_rounds", [])

    if round_number < 1 or round_number > len(completed_rounds):
        return redirect(url_for("index"))

    return render_template(
        "edit_round.html",
        round_number=round_number,
        matches=completed_rounds[round_number - 1]["matches"],
    )


@app.route("/update-round/<int:round_number>", methods=["POST"])
def update_round(round_number):
    completed_rounds = session.get("completed_rounds", [])

    if round_number < 1 or round_number > len(completed_rounds):
        return redirect(url_for("index"))

    completed_rounds[round_number - 1]["matches"] = apply_scores_from_form(
        completed_rounds[round_number - 1]["matches"],
        request.form,
    )

    session["completed_rounds"] = completed_rounds

    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    reset_tournament_state()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)