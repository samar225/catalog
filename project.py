from flask import Flask, render_template, request, redirect
from flask import jsonify, url_for, flash
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, Brand, MackeupItem, User
from flask import session as login_session
import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
from flask import make_response
import requests
from functools import wraps

app = Flask(__name__)

CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']
APPLICATION_NAME = "Mackeup Application"

# Connect to Database and create database session
engine = create_engine(
    'sqlite:///mackeupitemtypewithusers.db?check_same_thread=False')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


def login_required(f):
    @wraps(f)
    def x(*args, **kwargs):
        if 'username' not in login_session:
            return redirect('/login')
        return f(*args, **kwargs)
    return x


# Create anti-forgery state token
@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    # return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)


@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_access_token = login_session.get('access_token')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_access_token is not None and gplus_id == stored_gplus_id:
        response = make_response(
            json.dumps('Current user is already connected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']
    # ADD PROVIDER TO LOGIN SESSION
    login_session['provider'] = 'google'

    # See if a user exists, if it doesn't make a new one
    user_id = getUserID(login_session['email'])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = width: 300px; height: 300px;border-radius: 150px; \
    -webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("you are now logged in as %s" % login_session['username'])
    print "done!"
    return output

# User Helper Functions


def createUser(login_session):
    newUser = User(name=login_session['username'], email=login_session[
                   'email'], picture=login_session['picture'])
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def getUserInfo(user_id):
    user = session.query(User).filter_by(id=user_id).one()
    return user


def getUserID(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except BaseException:
        return None

# DISCONNECT - Revoke a current user's token and reset their login_session


@app.route('/gdisconnect')
def gdisconnect():
        # Only disconnect a connected user.
    access_token = login_session.get('access_token')
    if access_token is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] == '200':

        response = make_response(json.dumps('Successfully disconnected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response
    else:
        # For whatever reason, the given token was invalid.
        response = make_response(
            json.dumps('Failed to revoke token for given user.', 400))
        response.headers['Content-Type'] = 'application/json'
        return response


# JSON APIs to view Brand Information


@app.route('/brand/<int:brand_id>/mackeup/JSON')
def brandMackeupJSON(brand_id):
    brand = session.query(Brand).filter_by(id=brand_id).one()
    items = session.query(MackeupItem).filter_by(
        brand_id=brand_id).all()
    return jsonify(MackeupItem=[i.serialize for i in items])


@app.route('/brand/<int:brand_id>/mackeup/<int:mackeup_id>/JSON')
def mackeupItemJSON(brand_id, mackeup_id):
    Mackeup_Item = session.query(MackeupItem).filter_by(id=mackeup_id).one()
    return jsonify(Mackeup_Item=Mackeup_Item.serialize)


@app.route('/brand/JSON')
def brandJSON():
    brands = session.query(Brand).all()
    return jsonify(brands=[r.serialize for r in brands])

# Show all brands


@app.route('/')
@app.route('/brand/')
def showBrands():
    brands = session.query(Brand).order_by(asc(Brand.name))
    if 'username' not in login_session:
        return render_template('publicbrands.html', brands=brands)
    else:
        return render_template('brands.html', brands=brands)

# Create a new brand


@app.route('/brand/new/', methods=['GET', 'POST'])
@login_required
def newBrand():
    if request.method == 'POST':
        newBrand = Brand(
            name=request.form['name'], user_id=login_session['user_id'])
        session.add(newBrand)
        flash('New Brand %s Successfully Created' % newBrand.name)
        session.commit()
        return redirect(url_for('showBrands'))
    else:
        return render_template('newBrand.html')

# Edit a brand


@app.route('/brand/<int:brand_id>/edit/', methods=['GET', 'POST'])
@login_required
def editBrand(brand_id):
    editedBrand = session.query(Brand).filter_by(id=brand_id).one()
    if editedBrand.user_id != login_session['user_id']:
        return "<script>function myFunction() {alert('You are not \
        authorized to edit this brand. \
        Please create your own brand in order to edit.');}"
        "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        if request.form['name']:
            editedBrand.name = request.form['name']
            flash('Brand Successfully Edited %s' % editedBrand.name)
            return redirect(url_for('showBrands'))
    else:
        return render_template('editBrand.html', brand=editedBrand)

# Delete a brand


@app.route('/brand/<int:brand_id>/delete/', methods=['GET', 'POST'])
@login_required
def deleteBrand(brand_id):
    brandToDelete = session.query(Brand).filter_by(id=brand_id).one()

    if brandToDelete.user_id != login_session['user_id']:
        return "<script>function myFunction() {alert('You are not \
        authorized to delete this brand.\
         Please create your own brand in order to delete.');}</script>\
         <body onload='myFunction()'>"
    if request.method == 'POST':
        session.delete(brandToDelete)
        flash('%s Successfully Deleted' % brandToDelete.name)
        session.commit()
        return redirect(url_for('showBrands', brand=brand_id))
    else:
        return render_template('deleteBrand.html', brand=brandToDelete)

# Show a brand mackeup


@app.route('/brand/<int:brand_id>/')
@app.route('/brand/<int:brand_id>/mackeup/')
def showMackeup(brand_id):
    brand = session.query(Brand).filter_by(id=brand_id).one()
    creator = getUserInfo(brand.user_id)
    items = session.query(MackeupItem).filter_by(
        brand_id=brand_id).all()
    if 'username' not in login_session or creator.id != login_session['user_id']:
        return render_template('publicmackeup.html', items=items,
                               brand=brand, creator=creator)
    else:
        return render_template('mackeup.html', items=items,
                               brand=brand, creator=creator)

# Create a new mackeup item


@app.route('/brand/<int:brand_id>/mackeup/new/', methods=['GET', 'POST'])
@login_required
def newMackeupItem(brand_id):
    brand = session.query(Brand).filter_by(id=brand_id).one()
    if login_session['user_id'] != brand.user_id:
        return "<script>function myFunction() {alert('You are not \
        authorized to add mackeup items to this brand. \
        Please create your own brand in order to add items.');} \
        </script><body onload='myFunction()'>"
    if request.method == 'POST':
        newItem = MackeupItem(name=request.form['name'],
                              description=request.form['description'],
                              price=request.form['price'],
                              type=request.form['type'],
                              brand_id=brand_id,
                              user_id=brand.user_id)
        session.add(newItem)
        session.commit()
        flash('New Mackeup %s Item Successfully Created' % (newItem.name))
        return redirect(url_for('showMackeup', brand_id=brand_id))
    else:
        return render_template('newmackeupitem.html', brand_id=brand_id)

# Edit a mackeup item


@app.route(
    '/brand/<int:brand_id>/mackeup/<int:mackeup_id>/edit',
    methods=['GET', 'POST'])
@login_required
def editMackeupItem(brand_id, mackeup_id):
    editedItem = session.query(MackeupItem).filter_by(id=mackeup_id).one()
    brand = session.query(Brand).filter_by(id=brand_id).one()
    if login_session['user_id'] != brand.user_id:
        return "<script>function myFunction() {alert('You are not \
        authorized to edit \
        mackeup items to this brand. \
        Please create your own brand in order to edit items. \
        ');}</script><body onload='myFunction()'>"

    if request.method == 'POST':
        if request.form['name']:
            editedItem.name = request.form['name']
        if request.form['description']:
            editedItem.description = request.form['description']
        if request.form['price']:
            editedItem.price = request.form['price']
        if request.form['type']:
            editedItem.type = request.form['type']
        session.add(editedItem)
        session.commit()
        flash('Mackeup Item Successfully Edited')
        return redirect(url_for('showMackeup', brand_id=brand_id))
    else:
        return render_template(
            'editmackeupitem.html',
            brand_id=brand_id,
            mackeup_id=mackeup_id,
            item=editedItem)

# Delete a mackeup item


@app.route(
    '/brand/<int:brand_id>/mackeup/<int:mackeup_id>/delete',
    methods=[
        'GET',
        'POST'])
@login_required
def deleteMackeupItem(brand_id, mackeup_id):
    brand = session.query(Brand).filter_by(id=brand_id).one()
    itemToDelete = session.query(MackeupItem).filter_by(id=mackeup_id).one()
    if login_session['user_id'] != brand.user_id:
        return "<script>function myFunction() {alert( \
        'You are not authorized to delete mackeup \
         items to this brand. Please create your own brand in order \
         to delete items.');}</script><body onload='myFunction()'>"
    if request.method == 'POST':
        session.delete(itemToDelete)
        session.commit()
        flash('Mackeup Item Successfully Deleted')
        return redirect(url_for('showMackeup', brand_id=brand_id))
    else:
        return render_template('deletemackeupitem.html', item=itemToDelete)

# Disconnect based on provider


@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['access_token']
            del login_session['gplus_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect(url_for('showBrands'))

    else:
        flash("You were not logged in")
        return redirect(url_for('showBrands'))


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=5000)
