import logging
from faker import Faker
import asyncio
from typing import Dict, Optional
from random import choice
from quart import Quart, render_template, request, session, stream_with_context, url_for, redirect
from datastar_py.responses import make_datastar_quart_response
from collections import defaultdict
from bs4 import BeautifulSoup
import time


# Configure logging
logging.basicConfig(
    filename='perso.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Init
app = Quart(__name__)
app.secret_key = "secret_key"
fake = Faker()


# Arena and Home
class Arena:
    """Class representing a virtual arena for a streamer."""
    def __init__(
            self,
            channel: str,
            name: str,
            tiers: int = 8,
            seats_per_tier: int = 5,
            seats: Dict[str, Optional[str]] = None,
            user_seats: Dict[str, str] = None
            ):
        self.channel = channel
        self.name = name
        self.tiers = tiers
        self.seats_per_tier = seats_per_tier
        self.max_seats = tiers * seats_per_tier * 2  # 2 sides: left and right
        self.seats = seats if seats is not None else defaultdict(lambda: None)
        self.user_seats = user_seats if user_seats is not None else defaultdict(lambda: None)
        self.emotes = []  # [(user_id, emote_id, timestamp)]
        self.emote_scales = {}
        self.base_soup = None
        self.emotes_soup = None

        if not self.seats:
            for side in ['left', 'right']:
                for tier in range(self.tiers):
                    for seat in range(self.seats_per_tier):
                        self.seats[f"{side}_{tier}_{seat}"] = None

        self.generate_html()

    def process_emotes(self):
        """Pre-process emotes to determine scaling based on frequency."""
        current_time = time.time()
        # Only consider emotes from last 2 seconds for combo scaling
        recent_cutoff = current_time - 2

        # Count recent emotes by user and type
        user_emote_counts = defaultdict(int)
        for user_id, emote_id, timestamp in self.emotes:
            if timestamp >= recent_cutoff:
                user_emote_counts[(user_id, emote_id)] += 1

        # Calculate scales (max 2x for 5+ identical emotes)
        self.emote_scales = {}
        for (user_id, emote_id), count in user_emote_counts.items():
            scale = min(1 + (count - 1) * 0.1, 5.0)  # Increases by 0.25 per emote, max 2.0
            self.emote_scales[(user_id, emote_id)] = scale

    def generate_html(self):
        """Generate HTML for the arena with current seat occupancy."""
        left_seats = ""
        right_seats = ""

        for side in ['left', 'right']:
            for tier in range(self.tiers):
                tier_html = f'<div class="tier tier-{tier}">'
                for seat in range(self.seats_per_tier):
                    seat_id = f"{side}_{tier}_{seat}"
                    anchor_name = "--" + seat_id
                    if not self.seats[seat_id]:
                        seat_class = "empty-seat"
                        seat_content = ""
                        data_post = f'''data-on-click="@post('/move/{self.channel}/{seat_id}')"'''
                    else:
                        seat_class = "occupied-seat"
                        seat_content = "<div class='visitor-character'></div>"
                        data_post = ""

                    seat_html = f'''
                    <div id="{seat_id}" 
                    class="{seat_class}"
                    style="anchor-name: {anchor_name};"
                    {data_post}>
                    {seat_content}
                    </div>
                    '''
                    tier_html += seat_html
                tier_html += '</div>'

                if side == 'left':
                    left_seats += tier_html
                else:
                    right_seats += tier_html

        buttons_html = "".join(
            f'''<div 
            class="emote-button" 
            data-on-click="@post('/emote/{self.channel}/{i}')">
            <img src="/static/img/emotes/emote{i}.png" alt="Emote {i}">
            </div>'''
            for i in range(8)
        )

        raw_html = f'''
        <div id="arena-root" class="arena-container">
            <div class="emote-buttons">{buttons_html}</div>
            <div class="seating-area left-seats">\n{left_seats}</div>
            <div class="stream-container">
                <iframe src="https://player.twitch.tv/?channel={self.channel}&parent=XXX"
                        height="100%" width="100%" frameborder="0" allowfullscreen></iframe>
            </div>
            <div class="seating-area right-seats">\n{right_seats}</div>
            <div class="footer">made with love by ᓚᘏᗢ</div>
        </div>
        '''
        self.base_soup = BeautifulSoup(raw_html, 'html.parser')
        self.generate_emotes_html()

    def generate_emotes_html(self):
        """Generate HTML for emotes overlay."""
        self.remove_old_emotes()
        self.process_emotes()
        
        emotes_html = '<div id="emotes-overlay">'
        for user_id, emote_id, _ in self.emotes:
            if user_id in self.user_seats:
                seat_id = self.user_seats[user_id]
                scale = self.emote_scales.get((user_id, emote_id), 1.0)
                scale_style = f"scale: {scale};" if scale > 1.0 else ""
                emotes_html += f'''
                <div class="emote" style="position-anchor: --{seat_id}; {scale_style}">
                    <img src="/static/img/emotes/emote{emote_id}.png" alt="Emote {emote_id}">
                </div>
                '''
        emotes_html += '</div>'
        self.emotes_soup = BeautifulSoup(emotes_html, 'html.parser')

    def customize_html(self, user_id, parent):
        """Customize arena HTML for specific user."""
        user_soup = BeautifulSoup(str(self.base_soup), 'html.parser')
        
        # Update iframe src
        iframe = user_soup.find('iframe')
        if iframe:
            iframe['src'] = f"https://player.twitch.tv/?channel={self.channel}&parent={parent}"
        
        # Update user character
        seat_id = self.user_seats[user_id]
        seat_div = user_soup.find('div', id=seat_id)
        if seat_div:
            character_div = seat_div.find('div', class_='visitor-character')
            if character_div:
                character_div['class'] = 'user-character'
        
        return str(user_soup.prettify())

    def get_emotes_html(self):
        """Get the current emotes HTML."""
        return str(self.emotes_soup.prettify())

    def add_emote(self, user_id, emote_id):
        """Adds an emote to the emotes list."""
        timestamp = time.time()
        self.emotes.append((user_id, emote_id, timestamp))
        logger.info(f"User {user_id} sent emote {emote_id} in channel {self.channel}")

    def remove_old_emotes(self):
        """Removes emotes older than 10 seconds."""
        cutoff = time.time() - 10
        self.emotes = [(user_id, emote_id, timestamp) for user_id, emote_id, timestamp in self.emotes if timestamp >= cutoff]

    def update_html(self):
        """Update the stored HTML state"""
        self.generate_html()

    def get_random_free_seat(self):
        """Return a random available seat ID, or None if arena is full."""
        free_seats = [seat_id for seat_id, user_id in self.seats.items() if user_id is None]
        return choice(free_seats) if free_seats else None
        
    def count_occupants(self):
        """Count the number of occupied seats"""
        return len(self.user_seats)
    
    def add_user(self, user_id):
        """Add a user to a random empty seat"""
        if user_id in self.user_seats:
            self.remove_user(user_id)
            
        seat_id = self.get_random_free_seat()
        if not seat_id:
            return False  # Arena is full
            
        self.seats[seat_id] = user_id
        self.user_seats[user_id] = seat_id
        logger.info(f"User {user_id} joined arena {self.channel} at seat {seat_id}")
        return True
    
    def remove_user(self, user_id):
        """Remove a user from their seat"""
        if user_id in self.user_seats:
            seat_id = self.user_seats[user_id]
            self.seats[seat_id] = None
            del self.user_seats[user_id]

    def move_user(self, user_id, seat_id):
        """Move a user to a specific seat if available"""
        # Check if seat exists and is empty
        if seat_id not in self.seats or self.seats[seat_id] is not None:
            return False
            
        # free their current seat
        current_seat = self.user_seats[user_id]
        self.seats[current_seat] = None
            
        # Assign user to new seat
        self.seats[seat_id] = user_id
        self.user_seats[user_id] = seat_id
        return True

# HTML state
class StreamList:
    def __init__(self):
        self.html = None
        self.generate_html()
    
    def generate_html(self):
        """Generate HTML of arenas"""
        stream_list = ""
        for arena in ARENAS.values():
            occupants = arena.count_occupants()
            stream_list += f"""
                <a href="/arena/{arena.channel}" 
                class="streamer-button">
                    <span class="streamer-name">{arena.name}</span>
                    <span class="occupancy">({occupants}/{arena.max_seats})</span>
                </a>
            """
        
        html = f"""
            <div id="index-div" class="streamer-list">
                {stream_list}
            </div>
        """
        
        self.html = html
    
    def update_html(self):
        """Update the stream list HTML"""
        self.generate_html()


# Broadcasts
async def broadcast_stream_list():
    """Broadcast stream list updates to home users."""
    stream_list.update_html()
    logger.info(f"Broadcasting stream list update to users: {[user for user in connections['streamlist']]}")
    
    for user_id, queue in connections['streamlist'].items():
        await queue.put(stream_list.html)
        logger.debug(f"Stream list update sent to user {user_id}")

async def broadcast_arena(arena):
    """Broadcast arena base updates to users in the specific channel's arena."""
    arena.generate_html()
    logger.info(f"Broadcasting {arena.channel} base update to users: {[user for user in connections[arena.channel]]}")
    
    for user_id, queue in connections[arena.channel].items():
        parent = request.host.split(':')[0]
        await queue.put(("arena", arena.customize_html(user_id, parent)))
        logger.debug(f"Arena base update for {arena.channel} sent to user {user_id}")

async def broadcast_emotes(arena):
    """Broadcast emotes updates to users in the specific channel's arena."""
    arena.generate_emotes_html()
    logger.info(f"Broadcasting {arena.channel} emotes update to users: {[user for user in connections[arena.channel]]}")
    
    for user_id, queue in connections[arena.channel].items():
        await queue.put(("emotes", arena.get_emotes_html()))
        logger.debug(f"Emotes update for {arena.channel} sent to user {user_id}")

async def remove_from_broadcast(broadcast, user_id):
    """Try to remove user from broadcast."""
    try:
        del connections[broadcast][user_id]
        logger.info(f"User {user_id} removed from {broadcast} queue.")
    except KeyError:
        pass


# Beforeware
@app.before_serving
async def initialize_app():
    # Initialize arenas and stuff
    global ARENAS, stream_list, connections
    ARENAS = {
        "otplol_": Arena(channel="otplol_", name="OTP"),
        "claudeplayspokemon": Arena(channel="claudeplayspokemon", name="CPP")
    }
    stream_list = StreamList()
    connections = {'streamlist': {}}  # what -> {user_id: queue}
    connections.update({channel: {} for channel in ARENAS})
    logger.info("Application initialized")

@app.before_request
async def ensure_user_id():
    if 'user_id' not in session:
        session['user_id'] = fake.name()
        logger.info(f"New user created with ID: {session['user_id']}")
    logger.info(f"Request: {request.method} {request.path} - User: {session['user_id']}")


# App
@app.route("/")
async def home():
    logger.info(f"User {session['user_id']} accessed home page")
    return await render_template("index.html")

@app.route('/stream-list')
async def stream_list():
    user_id = session['user_id']
    logger.info(f"User {user_id} initiated stream list event stream")
    queue = asyncio.Queue()
    await queue.put(stream_list.html)
    connections['streamlist'].update({user_id: queue})

    @stream_with_context
    async def event(generator):
        try:
            while True:
                html = await connections['streamlist'][user_id].get()
                yield generator.merge_fragments([html])
        except asyncio.CancelledError:
            await remove_from_broadcast('streamlist', user_id)
            logger.info(f"User {user_id} closed stream list event stream")

    return await make_datastar_quart_response(event)

@app.route('/arena/<channel>')
async def arena(channel):
    user_id = session['user_id']
    logger.info(f"User {user_id} attempting to access arena: {channel}")
    
    if channel not in ARENAS:
        logger.warning(f"User {user_id} attempted to access invalid arena: {channel}")
        return redirect(url_for('home'))

    # remove user from streamlist queue    
    await remove_from_broadcast('streamlist', user_id)

    # Remove user from other arenas
    for other_channel, other_arena in ARENAS.items():
        if user_id in other_arena.user_seats and other_channel != channel:
            old_seat = other_arena.user_seats[user_id]
            logger.info(f"User {user_id} left arena {other_channel} from seat {old_seat}")
            other_arena.remove_user(user_id)
            other_arena.update_html()
            await broadcast_arena(other_arena)

    # Add to current arena
    current_arena = ARENAS[channel]
    if not current_arena.add_user(user_id):
        logger.warning(f"User {user_id} couldn't join arena {channel} - arena full")
        return redirect(url_for('home'))
    
    await broadcast_arena(current_arena)

    stream_list.update_html()
    await broadcast_stream_list()

    return await render_template("arena.html", channel=channel)

@app.route('/arena-stream/<channel>')
async def arena_stream(channel):
    user_id = session['user_id']
    logger.info(f"User {user_id} initiated arena stream for channel: {channel}")
    
    if channel not in ARENAS:
        logger.warning(f"User {user_id} attempted to stream invalid arena: {channel}")
        return "Invalid channel", 200

    arena = ARENAS[channel]
    
    # Handle page refresh: ensure user is in arena
    if user_id not in arena.user_seats:
        logger.info(f"User {user_id} refreshed page - re-adding to arena {channel}")
        if not arena.add_user(user_id):
            logger.warning(f"User {user_id} couldn't rejoin arena {channel} - arena full")
            return "Arena full", 200
        await broadcast_arena(arena)

    parent = request.host.split(':')[0]
    # reinit connection state
    queue = asyncio.Queue()
    await queue.put(("arena", arena.customize_html(user_id, parent)))
    connections[channel].update({user_id: queue})
    logger.info(f"Arena stream for {channel} initialized with parent: {parent}")

    @stream_with_context
    async def event(generator):
        try:
            while True:
                update_type, html = await connections[channel][user_id].get()
                if update_type == "arena":
                    custom_html = arena.customize_html(user_id, parent)
                    yield generator.merge_fragments([custom_html])
                elif update_type == "emotes":
                    yield generator.merge_fragments([html])
        except asyncio.CancelledError:
            logger.info(f"User {user_id} closed arena stream for {channel}")
            await remove_from_broadcast(channel, user_id)
            arena.remove_user(user_id)
            logger.info(f"User {user_id} removed from arena {channel}")
            arena.update_html()
            await broadcast_arena(arena)
            
            # Update stream list after user leaves
            stream_list.update_html()
            await broadcast_stream_list()

    return await make_datastar_quart_response(event)

@app.route('/move/<channel>/<seat_id>', methods=['POST'])
async def move_seat(channel, seat_id):
    """Move a user to a different seat in the arena."""
    user_id = session['user_id']
    
    if not seat_id or not channel or channel not in ARENAS:
        logger.warning(f"Invalid move request: User {user_id}, seat {seat_id}, channel {channel}")
        return "", 200
    
    # Attempt to move the user
    success = ARENAS[channel].move_user(user_id, seat_id)
    if success:
        # Update arena HTML and broadcast changes
        logger.info(f"User {user_id} moved to seat {seat_id} in channel {channel}")
        ARENAS[channel].update_html()
        await broadcast_arena(ARENAS[channel])
        return "", 200
    else:
        logger.warning(f"Failed to move user {user_id} to seat {seat_id} in channel {channel}")
        return "", 200

@app.route('/emote/<channel>/<i>', methods=['POST'])
async def send_emote(channel, i):
    """Send an emote."""
    user_id = session['user_id']
    logger.info(f"User {user_id} sent emote {i} in channel {channel}")
    if channel not in ARENAS:
        return "", 200

    arena = ARENAS[channel]
    if user_id not in arena.user_seats:
        return "", 200
    
    arena.add_emote(user_id, int(i))  # Use add_emote
    await broadcast_emotes(arena)  # Broadcast the changes
    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
