# -*- coding: utf-8 -*-
# Module: series_manager
# Author: user extension
# Created on: 5.6.2023
# License: AGPL v.3 https://www.gnu.org/licenses/agpl-3.0.html

import os
import io
import re
import json
import xbmc
import xbmcaddon
import xbmcgui
import xml.etree.ElementTree as ET
import unidecode

try:
    from urllib import urlencode
    from urlparse import parse_qsl
except ImportError:
    from urllib.parse import urlencode
    from urllib.parse import parse_qsl

try:
    from xbmc import translatePath
except ImportError:
    from xbmcvfs import translatePath

# Regular expressions for detecting episode patterns
EPISODE_PATTERNS = [
    r'[Ss](\d+)[Ee](\d+)',  # S01E01 format
    r'(\d+)x(\d+)',         # 1x01 format
    r'[Ee]pisode\s*(\d+)',  # Episode 1 format
    r'[Ee]p\s*(\d+)',       # Ep 1 format
    r'[Ee](\d+)',           # E1 format
    r'(\d+)\.\s*(\d+)'      # 1.01 format
]

class SeriesManager:
    def __init__(self, addon, profile):
        self.addon = addon
        self.profile = profile
        self.series_db_path = os.path.join(profile, 'series_db')
        self.ensure_db_exists()
        
    def ensure_db_exists(self):
        """Ensure that the series database directory exists"""
        try:
            if not os.path.exists(self.profile):
                os.makedirs(self.profile)
            if not os.path.exists(self.series_db_path):
                os.makedirs(self.series_db_path)
        except Exception as e:
            xbmc.log(f'YaWSP Series Manager: Error creating directories: {str(e)}', level=xbmc.LOGERROR)
    
    def _normalize(self, text):
        # Remove diacritics, replace spaces, dashes, underscores, lowercase
        if not text:
            return ''
        text = unidecode.unidecode(text)
        text = text.lower()
        text = text.replace('-', ' ')
        text = text.replace('_', ' ')
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        return text
    
    def search_series(self, series_name, api_function, token):
        """Search for episodes of a series"""
        series_data = {
            'name': series_name,
            'last_updated': xbmc.getInfoLabel('System.Date'),
            'seasons': {}
        }
        
        # Prepare normalized variants
        norm_name = self._normalize(series_name)
        name_nospaces = norm_name.replace(' ', '')
        name_with_underscores = norm_name.replace(' ', '_')
        name_with_dashes = norm_name.replace(' ', '-')
        
        # Define search queries to try (more variants)
        search_queries = [
            series_name,
            norm_name,
            name_nospaces,
            name_with_underscores,
            name_with_dashes,
            f"{series_name} season",
            f"{series_name} s01",
            f"{series_name} episode",
            f"{norm_name} season",
            f"{norm_name} s01",
            f"{norm_name} episode"
        ]
        
        all_results = []
        for query in search_queries:
            results = self._perform_search(query, api_function, token)
            for result in results:
                if result not in all_results and self._is_likely_episode(result['name'], series_name):
                    all_results.append(result)
        
        episodes = {}
        for item in all_results:
            season_num, episode_num = self._detect_episode_info(item['name'], series_name)
            if season_num is not None:
                season_num_str = str(season_num)
                episode_num_str = str(episode_num)
                if season_num_str not in episodes:
                    episodes[season_num_str] = {}
                if episode_num_str not in episodes[season_num_str]:
                    episodes[season_num_str][episode_num_str] = []
                episodes[season_num_str][episode_num_str].append({
                    'name': item['name'],
                    'ident': item['ident'],
                    'size': item.get('size', '0')
                })
        # Prevedu do formatu pro ulozeni (prvni jako hlavni, ostatni jako streams)
        series_data['seasons'] = {}
        for season_num, season in episodes.items():
            series_data['seasons'][season_num] = {}
            for episode_num, files in season.items():
                main = files[0]
                main['streams'] = files
                series_data['seasons'][season_num][episode_num] = main
        self._save_series_data(series_name, series_data)
        return series_data
    
    def _is_likely_episode(self, filename, series_name):
        # Use normalized comparison
        norm_filename = self._normalize(filename)
        norm_series = self._normalize(series_name)
        if norm_series not in norm_filename:
            return False
        for pattern in EPISODE_PATTERNS:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
        episode_keywords = [
            'episode', 'season', 'series', 'ep', 
            'complete', 'serie', 'disk'
        ]
        for keyword in episode_keywords:
            if keyword in norm_filename:
                return True
        return False
    
    def _perform_search(self, search_query, api_function, token):
        """Perform the actual search using the provided API function"""
        results = []
        
        # Call the Webshare API to search for the series
        response = api_function('search', {
            'what': search_query, 
            'category': 'video', 
            'sort': 'recent',
            'limit': 100,  # Get a good number of results to find episodes
            'offset': 0,
            'wst': token,
            'maybe_removed': 'true'
        })
        
        xml = ET.fromstring(response.content)
        
        # Check if the search was successful
        status = xml.find('status')
        if status is not None and status.text == 'OK':
            # Convert XML to a list of dictionaries
            for file in xml.iter('file'):
                item = {}
                for elem in file:
                    item[elem.tag] = elem.text
                results.append(item)
        
        return results
    
    def _detect_episode_info(self, filename, series_name):
        # Use normalized names for cleaning
        norm_filename = self._normalize(filename)
        norm_series = self._normalize(series_name)
        cleaned = norm_filename.replace(norm_series, '').strip()
        for pattern in EPISODE_PATTERNS:
            match = re.search(pattern, cleaned)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    return int(groups[0]), int(groups[1])
                elif len(groups) == 1:
                    return 1, int(groups[0])
        if 'season' in cleaned or 'serie' in cleaned:
            season_match = re.search(r'season\s*(\d+)', cleaned)
            if season_match:
                season_num = int(season_match.group(1))
                ep_match = re.search(r'(\d+)', cleaned.replace(season_match.group(0), ''))
                if ep_match:
                    return season_num, int(ep_match.group(1))
        return None, None
    
    def _save_series_data(self, series_name, series_data):
        """Save series data to the database"""
        safe_name = self._safe_filename(series_name)
        file_path = os.path.join(self.series_db_path, f"{safe_name}.json")
        
        try:
            with io.open(file_path, 'w', encoding='utf8') as file:
                try:
                    data = json.dumps(series_data, indent=2).decode('utf8')
                except AttributeError:
                    data = json.dumps(series_data, indent=2)
                file.write(data)
                file.close()
        except Exception as e:
            xbmc.log(f'YaWSP Series Manager: Error saving series data: {str(e)}', level=xbmc.LOGERROR)
    
    def load_series_data(self, series_name):
        """Load series data from the database"""
        safe_name = self._safe_filename(series_name)
        file_path = os.path.join(self.series_db_path, f"{safe_name}.json")
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with io.open(file_path, 'r', encoding='utf8') as file:
                data = file.read()
                file.close()
                try:
                    series_data = json.loads(data, "utf-8")
                except TypeError:
                    series_data = json.loads(data)
                return series_data
        except Exception as e:
            xbmc.log(f'YaWSP Series Manager: Error loading series data: {str(e)}', level=xbmc.LOGERROR)
            return None
    
    def get_all_series(self):
        """Get a list of all saved series"""
        series_list = []
        
        try:
            for filename in os.listdir(self.series_db_path):
                if filename.endswith('.json'):
                    series_name = os.path.splitext(filename)[0]
                    # Convert safe filename back to proper name (rough conversion)
                    proper_name = series_name.replace('_', ' ')
                    series_list.append({
                        'name': proper_name,
                        'filename': filename,
                        'safe_name': series_name
                    })
        except Exception as e:
            xbmc.log(f'YaWSP Series Manager: Error listing series: {str(e)}', level=xbmc.LOGERROR)
        
        return series_list
    
    def _safe_filename(self, name):
        """Convert a series name to a safe filename"""
        # Replace problematic characters
        safe = re.sub(r'[^\w\-_\. ]', '_', name)
        return safe.lower().replace(' ', '_')

# Utility functions for the UI layer
def get_url(**kwargs):
    """Create a URL for calling the plugin recursively"""
    from yawsp import _url
    return '{0}?{1}'.format(_url, urlencode(kwargs, 'utf-8'))

def create_series_menu(series_manager, handle):
    """Create the series selection menu"""
    import xbmcplugin
    
    # Add "Search for new series" option
    listitem = xbmcgui.ListItem(label="Hledat novy serial")
    listitem.setArt({'icon': 'DefaultAddSource.png'})
    xbmcplugin.addDirectoryItem(handle, get_url(action='series_search'), listitem, True)
    
    # List existing series
    series_list = series_manager.get_all_series()
    for series in series_list:
        listitem = xbmcgui.ListItem(label=series['name'])
        listitem.setArt({'icon': 'DefaultFolder.png'})
        xbmcplugin.addDirectoryItem(handle, get_url(action='series_detail', series_name=series['name']), listitem, True)
    
    xbmcplugin.endOfDirectory(handle)

def create_seasons_menu(series_manager, handle, series_name):
    """Create menu of seasons for a series"""
    import xbmcplugin
    
    series_data = series_manager.load_series_data(series_name)
    if not series_data:
        xbmcgui.Dialog().notification('YaWSP', 'Data serialu nenalezena', xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    
    # Add "Refresh series" option
    listitem = xbmcgui.ListItem(label="Aktualizovat serial")
    listitem.setArt({'icon': 'DefaultAddonsSearch.png'})
    xbmcplugin.addDirectoryItem(handle, get_url(action='series_refresh', series_name=series_name), listitem, True)
    
    # List seasons
    for season_num in sorted(series_data['seasons'].keys(), key=int):
        season_name = f"Rada {season_num}"
        listitem = xbmcgui.ListItem(label=season_name)
        listitem.setArt({'icon': 'DefaultFolder.png'})
        xbmcplugin.addDirectoryItem(handle, get_url(action='series_season', series_name=series_name, season=season_num), listitem, True)
    
    xbmcplugin.endOfDirectory(handle)

def create_episodes_menu(series_manager, handle, series_name, season_num):
    """Create menu of episodes for a season"""
    import xbmcplugin
    
    series_data = series_manager.load_series_data(series_name)
    if not series_data or str(season_num) not in series_data['seasons']:
        xbmcgui.Dialog().notification('YaWSP', 'Data sezony nenalezena', xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    
    season_num = str(season_num)
    season = series_data['seasons'][season_num]
    for episode_num in sorted(season.keys(), key=int):
        episode = season[episode_num]
        episode_name = f"Epizoda {episode_num} - {episode['name']}"
        listitem = xbmcgui.ListItem(label=episode_name)
        listitem.setArt({'icon': 'DefaultVideo.png'})
        listitem.setProperty('IsPlayable', 'true')
        url = get_url(action='play', ident=episode['ident'], name=episode['name'])
        # Kontextove menu pro vyber streamu
        commands = [("Vybrat stream", 'RunPlugin(' + get_url(action='select_stream', series_name=series_name, season=season_num, episode=episode_num) + ')')]
        listitem.addContextMenuItems(commands)
        xbmcplugin.addDirectoryItem(handle, url, listitem, False)
    
    xbmcplugin.endOfDirectory(handle) 