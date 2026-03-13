"""
sample-app/app.py
-----------------
A minimal Flask app to verify the QA pipeline works end-to-end.
Run locally: python app.py
"""

from flask import Flask, jsonify, request, abort

app = Flask(__name__)

# ── In-memory "database" ───────────────────────────────────────────────────────
_items = {
    1: {"id": 1, "name": "Widget A", "price": 9.99,  "in_stock": True},
    2: {"id": 2, "name": "Widget B", "price": 19.99, "in_stock": False},
}
_next_id = 3


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Smoke test endpoint."""
    return jsonify({"status": "ok", "service": "sample-app"}), 200


@app.route("/")
def root():
    return jsonify({"message": "Sample QA App", "version": "1.0.0"}), 200


@app.route("/api/items", methods=["GET"])
def list_items():
    """List all items. Supports ?in_stock=true filter."""
    in_stock_filter = request.args.get("in_stock")
    items = list(_items.values())
    if in_stock_filter is not None:
        flag  = in_stock_filter.lower() == "true"
        items = [i for i in items if i["in_stock"] == flag]
    return jsonify({"items": items, "count": len(items)}), 200


@app.route("/api/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    item = _items.get(item_id)
    if not item:
        abort(404)
    return jsonify(item), 200


@app.route("/api/items", methods=["POST"])
def create_item():
    global _next_id
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body required"}), 400
    if "name" not in data:
        return jsonify({"error": "Field 'name' is required"}), 400

    item = {
        "id":       _next_id,
        "name":     data["name"],
        "price":    float(data.get("price", 0.0)),
        "in_stock": bool(data.get("in_stock", True)),
    }
    _items[_next_id] = item
    _next_id += 1
    return jsonify(item), 201


@app.route("/api/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    if item_id not in _items:
        abort(404)
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body required"}), 400

    item = _items[item_id]
    item["name"]     = data.get("name",     item["name"])
    item["price"]    = float(data.get("price", item["price"]))
    item["in_stock"] = bool(data.get("in_stock", item["in_stock"]))
    return jsonify(item), 200


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    if item_id not in _items:
        abort(404)
    deleted = _items.pop(item_id)
    return jsonify({"deleted": deleted}), 200


@app.route("/api/echo", methods=["POST"])
def echo():
    """Echo back whatever JSON is sent — useful for testing."""
    data = request.get_json(silent=True) or {}
    return jsonify({"echo": data}), 200


# ── Error handlers ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)
