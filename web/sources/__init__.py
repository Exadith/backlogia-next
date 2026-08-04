# sources package
# Provider scripts for fetching game libraries from various stores

from .steam import get_steam_library
from .epic import get_epic_library_legendary, is_legendary_installed, check_authentication
from .gog import get_gog_library
from .itch import get_auth_token, get_owned_games
from .humble import get_humble_library
from .battlenet import get_battlenet_library
from .amazon import get_amazon_library, is_nile_installed, check_auth_status, start_auth, complete_auth, logout
