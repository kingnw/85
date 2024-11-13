# app.py

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from auth import auth_blueprint
from models import db, User, UserMovies, Review
from recommendation import get_recommended_movies, get_movie_recommendations
from utils import get_movie_details, calculate_avg_rating, get_similar_movie_ratings
from tmdb_helpers import get_top_rated_movies, get_new_released_movies, get_trending_movies, get_genres, search_movie
import os
from datetime import datetime
from recommendation import get_personalized_recommendations


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

# Configure SQLAlchemy database URI
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy and Flask-Login
db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

# Load user callback for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Register the auth blueprint
app.register_blueprint(auth_blueprint, url_prefix='/auth')

# --------------------- Autocomplete Route ---------------------

@app.route('/autocomplete', methods=['GET'])
def autocomplete():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])

    suggestions = [{'label': movie['title'], 'value': movie['title'], 'id': movie['id']} for movie in search_movie(query)[:10]]
    return jsonify(suggestions)

# --------------------- Movie Details Route ---------------------
# Route to submit a rating for a movie
@app.route('/rate_movie', methods=['POST'])
@login_required
def rate_movie():
    movie_id = request.json.get('movie_id')
    rating = request.json.get('rating')
    
    if not movie_id or not rating:
        return jsonify({"message": "Invalid data"}), 400

    try:
        rating_value = float(rating)
        if not (0 <= rating_value <= 5):
            return jsonify({"message": "Rating must be between 0 and 5"}), 400

        # Check if the user has already rated this movie
        review = Review.query.filter_by(user_id=current_user.id, movie_id=movie_id).first()
        if review:
            review.rating = rating_value
            review.created_at = datetime.utcnow()
        else:
            review = Review(user_id=current_user.id, movie_id=movie_id, rating=rating_value)
            db.session.add(review)

        db.session.commit()
        return jsonify({"message": "Rating submitted successfully!"}), 200
    except ValueError:
        return jsonify({"message": "Invalid rating value"}), 400

# Route to display movie details with reviews and content-based recommendations
@app.route('/movie/<int:movie_id>', methods=['GET', 'POST'])
def movie_details(movie_id):
    movie = get_movie_details(movie_id)
    if not movie:
        return render_template('404.html'), 404

    reviews = Review.query.filter_by(movie_id=movie_id).order_by(Review.created_at.desc()).all()
    avg_rating = calculate_avg_rating(movie_id)

    # Personalized recommendations if user is authenticated
    if current_user.is_authenticated:
        recommendations = get_recommended_movies(current_user)
    else:
        recommendations = get_movie_recommendations(movie['title'])  # Fallback to content-based recommendations
    
    # Calculate average ratings for each recommended movie
    recommendations = get_similar_movie_ratings(recommendations)

    if request.method == 'POST' and current_user.is_authenticated:
        rating = request.form.get('rating')
        review_text = request.form.get('review_text')

        if rating:
            try:
                rating_value = float(rating)
                if 0 <= rating_value <= 10:
                    existing_review = Review.query.filter_by(user_id=current_user.id, movie_id=movie_id).first()
                    if existing_review:
                        existing_review.rating = rating_value
                        existing_review.review_text = review_text
                        existing_review.created_at = datetime.utcnow()
                    else:
                        new_review = Review(user_id=current_user.id, movie_id=movie_id, rating=rating_value, review_text=review_text)
                        db.session.add(new_review)
                    db.session.commit()
                    flash("Review submitted successfully!", "success")
                else:
                    flash("Rating must be between 0 and 10.", "warning")
            except ValueError:
                flash("Invalid rating value.", "danger")
            return redirect(url_for('movie_details', movie_id=movie_id))
        else:
            flash("Please provide a rating.", "warning")

    return render_template('movie_details.html', movie=movie, reviews=reviews, avg_rating=avg_rating, recommendations=recommendations)

# --------------------- Watchlist and Favorites Routes ---------------------
@app.route('/<category>/add/<int:movie_id>', methods=['POST'])
@login_required
def add_movie(category, movie_id):
    if category not in ['watchlist', 'favorites']:
        flash("Invalid category.", "danger")
        return redirect(url_for('index'))

    existing_entry = UserMovies.query.filter_by(user_id=current_user.id, movie_id=movie_id, category=category).first()
    if not existing_entry:
        new_entry = UserMovies(user_id=current_user.id, movie_id=movie_id, category=category)
        try:
            db.session.add(new_entry)
            db.session.commit()
            flash(f"Movie added to {category}.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding movie to {category}: {str(e)}", "danger")
    else:
        flash("Movie is already in your list.", "info")

    return redirect(url_for(f'view_{category}'))

@app.route('/<category>/remove/<int:movie_id>', methods=['POST'])
@login_required
def remove_movie(category, movie_id):
    if category not in ['watchlist', 'favorites']:
        flash("Invalid category.", "danger")
        return redirect(url_for('index'))
    
    entry = UserMovies.query.filter_by(user_id=current_user.id, movie_id=movie_id, category=category).first()
    if entry:
        try:
            db.session.delete(entry)
            db.session.commit()
            flash(f"Movie removed from {category}.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error removing movie from {category}: {str(e)}", "danger")
    else:
        flash("Movie not found in your list.", "warning")
    
    return redirect(url_for(f'view_{category}'))

@app.route('/watchlist')
@login_required
def view_watchlist():
    watchlist_movies = UserMovies.query.filter_by(user_id=current_user.id, category='watchlist').all()
    movies = [get_movie_details(movie.movie_id) for movie in watchlist_movies if get_movie_details(movie.movie_id)]
    if not movies:
        flash("Your watchlist is empty.", "info")
    return render_template('watchlist.html', movies=movies, category="Watchlist")

@app.route('/favorites')
@login_required
def view_favorites():
    favorite_movies = UserMovies.query.filter_by(user_id=current_user.id, category='favorites').all()
    movies = [get_movie_details(movie.movie_id) for movie in favorite_movies if get_movie_details(movie.movie_id)]
    if not movies:
        flash("Your favorites list is empty.", "info")
    return render_template('favorites.html', movies=movies, category="Favorites")

# --------------------- Filter Watchlist and Favorites Routes ---------------------

@app.route('/filter_watchlist', methods=['GET'])
@login_required
def filter_watchlist():
    sortby = request.args.get('sortby')
    movies = get_filtered_watchlist(current_user.id, sortby)
    return render_template('watchlist.html', movies=movies, category="Watchlist")

@app.route('/filter_favorites', methods=['GET'])
@login_required
def filter_favorites():
    sortby = request.args.get('sortby')
    movies = get_filtered_watchlist(current_user.id, sortby, category='favorites')
    return render_template('favorites.html', movies=movies, category="Favorites")

# Helper function to get filtered watchlist or favorites
def get_filtered_watchlist(user_id, sortby, category='watchlist'):
    user_movies = UserMovies.query.filter_by(user_id=user_id, category=category).all()
    movies = [get_movie_details(movie.movie_id) for movie in user_movies if get_movie_details(movie.movie_id)]
    
    # Apply sorting based on sortby parameter
    if sortby:
        if sortby == 'rating_desc':
            movies.sort(key=lambda x: x.get('rating', 0), reverse=True)
        elif sortby == 'rating_asc':
            movies.sort(key=lambda x: x.get('rating', 0))
        elif sortby == 'release_date_desc':
            movies.sort(key=lambda x: x.get('release_date', ''), reverse=True)
        elif sortby == 'release_date_asc':
            movies.sort(key=lambda x: x.get('release_date', ''))
        elif sortby == 'title_asc':
            movies.sort(key=lambda x: x.get('title', '').lower())
        elif sortby == 'title_desc':
            movies.sort(key=lambda x: x.get('title', '').lower(), reverse=True)
    
    return movies
# --------------------- Top-Rated, New Released, Trending Routes ---------------------

# Route to display top-rated movies
@app.route('/top-rated')
def top_rated():
    top_rated_movies = get_top_rated_movies()
    genres = get_genres()
    return render_template('top_rated.html', top_rated_movies=top_rated_movies, genres=genres)

# Route to display newly released movies
@app.route('/new-released')
def new_released():
    new_released_movies = get_new_released_movies()
    genres = get_genres()
    return render_template('new_released.html', new_released_movies=new_released_movies, genres=genres)

# Route to display trending movies
@app.route('/trending')
def trending():
    trending_movies = get_trending_movies()
    genres = get_genres()
    return render_template('trending.html', trending_movies=trending_movies, genres=genres)

# --------------------- Recommendation Routes ---------------------

@app.route('/personalized')
@login_required
def personalized_recommendations():
    recommendations = get_personalized_recommendations(current_user)
    return render_template('personalized.html', recommendations=recommendations, show_flash_messages=False)


@app.route('/recommend', methods=['POST'])
def recommend():
    movie_title = request.form.get('movie_title', '').strip()
    release_year = request.form.get('release_year', '').strip()
    rating = request.form.get('rating', '').strip()
    genre = request.form.get('genre', '').strip()
    language = request.form.get('language', '').strip()
    sort_by = request.form.get('sort_by', '').strip()

    filters = {
        'release_year': release_year,
        'rating': rating,
        'genre': genre,
        'language': language,
        'sort_by': sort_by
    }

    genres_list = get_genres()
    search_results = search_movie(movie_title, filters=filters)

    if current_user.is_authenticated:
        recommendations = get_recommended_movies(current_user)
    else:
        recommendations = get_recommended_movies()

    trending_movies = get_trending_movies()
    most_watched_movies = get_top_rated_movies()
    new_released_movies = get_new_released_movies()

    return render_template(
        'index.html',
        search_results=search_results,
        recommendations=recommendations,
        search_query=movie_title,
        filters=filters,
        genres=genres_list,
        trending_movies=trending_movies,
        most_watched_movies=most_watched_movies,
        new_released_movies=new_released_movies
    )

@app.route('/')
def index():
    recommendations = get_recommended_movies(current_user)
    genres = get_genres()
    trending_movies = get_trending_movies()
    most_watched_movies = get_top_rated_movies()
    new_released_movies = get_new_released_movies()
    return render_template(
        'index.html',
        recommendations=recommendations,
        genres=genres,
        trending_movies=trending_movies,
        most_watched_movies=most_watched_movies,
        new_released_movies=new_released_movies
    )

# Initialize database tables if they do not exist
with app.app_context():
    db.create_all()

# Start the Flask app
if __name__ == '__main__':
    app.run(debug=True, port=5004)

