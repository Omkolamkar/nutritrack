import azure.functions as func
import json, os
from datetime import datetime, date
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from bson import ObjectId
from nutrition_data import search_food, get_nutrition

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

MONGO_URI = os.environ["MONGO_URI"]
client = MongoClient(MONGO_URI, server_api=ServerApi("1"), serverSelectionTimeoutMS=5000)
db = client["nutritrack"]
logs_col = db["food_logs"]
goals_col = db["user_goals"]

DEFAULT_GOALS = {"calories": 2000, "protein": 50, "carbs": 250, "fat": 65, "fiber": 25, "sodium": 2300}

def today_str():
    return date.today().isoformat()

def json_serial(obj):
    if isinstance(obj, (datetime, date)): return obj.isoformat()
    if isinstance(obj, ObjectId): return str(obj)
    raise TypeError(f"Not serializable: {type(obj)}")

def jresp(data, status=200):
    return func.HttpResponse(
        json.dumps(data, default=json_serial),
        status_code=status,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@app.route(route="search", methods=["GET"])
def api_search(req: func.HttpRequest) -> func.HttpResponse:
    q = req.params.get("q", "")
    return jresp(search_food(q) if q else [])


@app.route(route="today", methods=["GET"])
def api_today(req: func.HttpRequest) -> func.HttpResponse:
    return jresp(list(logs_col.find({"date": today_str()})))


@app.route(route="summary", methods=["GET"])
def api_summary(req: func.HttpRequest) -> func.HttpResponse:
    d = req.params.get("date", today_str())
    entries = list(logs_col.find({"date": d}))
    keys = ["calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium"]
    totals = {k: round(sum(e.get(k, 0) for e in entries), 1) for k in keys}
    goals = goals_col.find_one({}, {"_id": 0}) or DEFAULT_GOALS
    meals = {}
    for e in entries:
        m = e.get("meal", "other")
        if m not in meals:
            meals[m] = {k: 0.0 for k in keys}
        for k in keys:
            meals[m][k] += e.get(k, 0)
    meals = {m: {k: round(v, 1) for k, v in mv.items()} for m, mv in meals.items()}
    return jresp({"totals": totals, "goals": goals, "meals": meals,
                  "entry_count": len(entries), "date": d})


@app.route(route="goals", methods=["GET"])
def api_goals_get(req: func.HttpRequest) -> func.HttpResponse:
    return jresp(goals_col.find_one({}, {"_id": 0}) or DEFAULT_GOALS)


@app.route(route="goals", methods=["POST"])
def api_goals_post(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()
    allowed = ["calories", "protein", "carbs", "fat", "fiber", "sodium"]
    payload = {k: float(body[k]) for k in allowed if k in body}
    if not payload:
        return jresp({"error": "No valid fields"}, 400)
    goals_col.delete_many({})
    goals_col.insert_one(payload)
    return jresp({"success": True, "goals": payload})


@app.route(route="history", methods=["GET"])
def api_history(req: func.HttpRequest) -> func.HttpResponse:
    pipeline = [
        {"$group": {"_id": "$date", "calories": {"$sum": "$calories"},
                    "protein": {"$sum": "$protein"}, "carbs": {"$sum": "$carbs"},
                    "fat": {"$sum": "$fat"}, "fiber": {"$sum": "$fiber"}}},
        {"$sort": {"_id": -1}}, {"$limit": 7}
    ]
    history = list(logs_col.aggregate(pipeline))
    for h in history:
        h["date"] = h.pop("_id")
        for k in ["calories", "protein", "carbs", "fat", "fiber"]:
            h[k] = round(h[k], 1)
    return jresp(history)


@app.route(route="add-food", methods=["POST"])
def api_add_food(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()
    food = body.get("food", "").lower().strip()
    qty = float(body.get("quantity", 100))
    meal = body.get("meal", "other")
    nutrition = get_nutrition(food, qty)
    if not nutrition:
        return jresp({"error": f"Food '{food}' not found"}, 404)
    doc = {**nutrition, "meal": meal, "date": today_str(), "timestamp": datetime.utcnow()}
    r = logs_col.insert_one(doc)
    doc["_id"] = str(r.inserted_id)
    return jresp({"success": True, "entry": doc})


@app.route(route="entry/{entry_id}", methods=["DELETE"])
def api_delete_entry(req: func.HttpRequest) -> func.HttpResponse:
    entry_id = req.route_params.get("entry_id")
    try:
        r = logs_col.delete_one({"_id": ObjectId(entry_id)})
        return jresp({"success": True} if r.deleted_count else {"error": "Not found"},
                     200 if r.deleted_count else 404)
    except Exception as e:
        return jresp({"error": str(e)}, 400)


@app.route(route="today/clear", methods=["DELETE"])
def api_clear_today(req: func.HttpRequest) -> func.HttpResponse:
    r = logs_col.delete_many({"date": today_str()})
    return jresp({"success": True, "deleted": r.deleted_count})