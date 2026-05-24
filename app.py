import os
from functools import wraps
from urllib.parse import quote

import requests
from bson.objectid import ObjectId
from flask import Flask, flash, redirect, render_template, request, session, url_for
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = client["flask_auth_db"]
users = db["users"]
searches = db["searches"]

indexes_ready = False

HTTP_TIMEOUT = 8
HTTP_HEADERS = {
    "User-Agent": "FlaskMongoSpaceDashboard/1.0 (local learning project)"
}

THREE_D_MODELS = [
    {
        "title": "Moon 3D Model",
        "keywords": ["moon", "lunar", "artemis"],
        "source": "NASA Scientific Visualization Studio",
        "page_url": "https://svs.gsfc.nasa.gov/14959",
        "model_url": "https://svs.gsfc.nasa.gov/vis/a010000/a014900/a014959/moon_small.glb",
    },
    {
        "title": "Space Shuttle 3D Model",
        "keywords": ["space shuttle", "shuttle", "orbiter"],
        "source": "NASA 3D Resources",
        "page_url": "https://science.nasa.gov/3d-resources/space-shuttle-b/",
        "model_url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/space-shuttle-%28b%29/Space%20Shuttle%20%28B%29.glb",
    },
    {
        "title": "Explorer Jupiter-C Rocket 3D Model",
        "keywords": ["jupiter-c", "jupiter c", "explorer", "rocket"],
        "source": "NASA 3D Resources",
        "page_url": "https://science.nasa.gov/?p=490211",
        "model_url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/explorer-jupiter-c-rocket/Explorer%20Jupiter-C%20Rocket.glb",
    },
    {
        "title": "Redstone 3 Freedom 7 3D Model",
        "keywords": ["redstone", "freedom 7", "mercury-redstone"],
        "source": "NASA 3D Resources",
        "page_url": "https://science.nasa.gov/3d-resources/redstone-3-freedom-7/",
        "model_url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/redstone-3-%28freedom-7%29/Redstone%203%20%28Freedom%207%29.glb",
    },
    {
        "title": "Mars Opportunity Rover 3D Model",
        "keywords": ["opportunity", "mars rover", "rover", "mer-b"],
        "source": "NASA 3D Resources",
        "page_url": "https://science.nasa.gov/3d-resources/mars-exploration-rover-opportunity-mer-b",
        "model_url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/mars-exploration-rover---opportunity-%28mer-b%29/Mars%20Exploration%20Rover%20-%20Opportunity%20%28MER-B%29.glb",
    },
    {
        "title": "Terra Satellite 3D Model",
        "keywords": ["terra", "satellite", "earth observing"],
        "source": "NASA 3D Resources",
        "page_url": "https://science.nasa.gov/3d-resources/terra/",
        "model_url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/terra/Terra.glb",
    },
]


def ensure_indexes():
    global indexes_ready
    if not indexes_ready:
        users.create_index("email", unique=True)
        searches.create_index("user_id")
        indexes_ready = True


def get_wikipedia_summary(query):
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(query)}"
    response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)

    if response.status_code == 404:
        return None

    response.raise_for_status()
    data = response.json()

    if data.get("type") == "disambiguation":
        return {
            "title": data.get("title", query),
            "extract": data.get("extract", "This search matched multiple topics."),
            "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
            "image": data.get("thumbnail", {}).get("source"),
        }

    return {
        "title": data.get("title", query),
        "extract": data.get("extract"),
        "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
        "image": data.get("thumbnail", {}).get("source"),
    }


def get_nasa_images(query, limit=9):
    params = {"q": query, "media_type": "image", "page_size": limit}
    response = requests.get(
        "https://images-api.nasa.gov/search",
        params=params,
        headers=HTTP_HEADERS,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    items = response.json().get("collection", {}).get("items", [])

    images = []
    for item in items:
        data = item.get("data", [{}])[0]
        links = item.get("links", [])
        thumbnail = links[0].get("href") if links else None

        if not thumbnail:
            continue

        images.append(
            {
                "title": data.get("title", "NASA image"),
                "description": data.get("description", ""),
                "date": data.get("date_created", "")[:10],
                "nasa_id": data.get("nasa_id"),
                "thumbnail": thumbnail,
                "href": item.get("href"),
            }
        )

    return images


def find_3d_models(query):
    normalized_query = query.lower()
    matches = []

    for model in THREE_D_MODELS:
        if any(keyword in normalized_query for keyword in model["keywords"]):
            matches.append(model)

    return matches


def search_space_data(query):
    result = {
        "query": query,
        "summary": None,
        "images": [],
        "models": find_3d_models(query),
        "error": None,
    }

    try:
        result["summary"] = get_wikipedia_summary(query)
    except requests.RequestException:
        result["error"] = "Could not fetch Wikipedia data right now."

    try:
        result["images"] = get_nasa_images(query)
    except requests.RequestException:
        if result["error"]:
            result["error"] += " NASA images are also unavailable."
        else:
            result["error"] = "Could not fetch NASA images right now."

    return result


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        try:
            ensure_indexes()
        except PyMongoError:
            flash("Could not connect to MongoDB. Make sure the local database is running.", "danger")
            return render_template("register.html")

        try:
            if users.find_one({"email": email}):
                flash("An account with this email already exists.", "danger")
                return render_template("register.html")

            user = {
                "name": name,
                "email": email,
                "password": generate_password_hash(password, method="pbkdf2:sha256"),
            }
            result = users.insert_one(user)
        except PyMongoError:
            flash("Could not save the account. Please check MongoDB and try again.", "danger")
            return render_template("register.html")

        session["user_id"] = str(result.inserted_id)
        session["user_name"] = name
        flash("Registration successful.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        try:
            user = users.find_one({"email": email})
        except PyMongoError:
            flash("Could not connect to MongoDB. Make sure the local database is running.", "danger")
            return render_template("login.html")

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session["user_id"] = str(user["_id"])
        session["user_name"] = user["name"]
        flash("Login successful.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    query = request.args.get("q", "").strip()
    space_data = None

    try:
        user = users.find_one({"_id": ObjectId(session["user_id"])})
    except PyMongoError:
        flash("Could not connect to MongoDB. Make sure the local database is running.", "danger")
        return redirect(url_for("login"))

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "warning")
        return redirect(url_for("login"))

    if query:
        space_data = search_space_data(query)
        try:
            searches.insert_one(
                {
                    "user_id": session["user_id"],
                    "query": query,
                    "image_count": len(space_data["images"]),
                    "model_count": len(space_data["models"]),
                }
            )
        except PyMongoError:
            pass

    return render_template("dashboard.html", user=user, query=query, space_data=space_data)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


@app.route("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "mongodb": "connected"}
    except PyMongoError:
        return {"status": "error", "mongodb": "not connected"}, 503


if __name__ == "__main__":
    app.run(debug=True)
