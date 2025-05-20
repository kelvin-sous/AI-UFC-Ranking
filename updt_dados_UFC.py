import string
import datetime
import re
import os
import logging
import time
from typing import Optional
import json

import pint
from bs4 import BeautifulSoup
import requests
from difflib import SequenceMatcher

PROJECT_NAME = 'scrape_ufc_stats'
LOGGER = None

CACHE_PATH = os.path.join(os.getcwd(), 'data', 'interim', 'fighter_links_cache.json')

def setup_basic_file_paths(project_name: str):
    base_folder = os.getcwd()
    data_folder = os.path.join(base_folder, 'data')
    raw_folder = os.path.join(data_folder, 'raw')
    interim_folder = os.path.join(data_folder, 'interim')
    log_file_path = os.path.join(base_folder, f'{project_name}.log')

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(raw_folder, exist_ok=True)
    os.makedirs(interim_folder, exist_ok=True)

    return base_folder, data_folder, raw_folder, interim_folder, log_file_path

def setup_logger(log_file_path: str):
    logger = logging.getLogger(PROJECT_NAME)
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger

def basic_request(url: str, logger: Optional[logging.Logger] = None, retries: int = 3, delay: int = 5) -> str:
    attempt = 0
    while attempt < retries:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.text
            else:
                if logger:
                    logger.warning(f"Status code {response.status_code} for URL: {url}")
                attempt += 1
                time.sleep(delay)
        except Exception as e:
            if logger:
                logger.warning(f"Exception during request: {e}")
            attempt += 1
            time.sleep(delay)
    raise RuntimeError(f"Failed to fetch URL: {url}")

def format_error(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)}"

def extract_fighter_pagelinks_from_serp(html: str) -> set[str]:
    soup = BeautifulSoup(html, 'html.parser')
    tags = soup.select('tr.b-statistics__table-row a')
    links = [tag.get('href') for tag in tags if tag.get('href')]
    return set(links)

def get_fighters_by_letter(letter: str) -> set[str]:
    url = f'http://ufcstats.com/statistics/fighters?char={letter}&page=all'
    try:
        html = basic_request(url, LOGGER)
        return extract_fighter_pagelinks_from_serp(html)
    except RuntimeError:
        return set()

def similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.8

def get_cached_fighters(letter: str) -> set[str]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            return set(cache.get(letter.upper(), []))
    return set()

def update_cache(letter: str, links: set[str]):
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    cache[letter.upper()] = list(links)
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)

def extract_bio_data(soup: BeautifulSoup, fighter_name: str) -> dict:
    try:
        physicial_data = soup.select_one(
            '.b-list__info-box.b-list__info-box_style_small-width')\
            .get_text(strip=True, separator='_').split('_')

        height = physicial_data[1] if len(physicial_data) > 1 else None
        weight = physicial_data[3] if len(physicial_data) > 3 else None
        reach = physicial_data[5] if len(physicial_data) > 5 else None
        stance = physicial_data[7] if len(physicial_data) > 7 else None
        dob = physicial_data[9] if len(physicial_data) > 9 else None

        def format_weight_to_kg(weight: str) -> Optional[float]:
            match = re.match(r'(\d+)', weight)
            return round(int(match.group(0)) * 0.453592, 2) if match else None

        weight_in_kg = format_weight_to_kg(weight) if weight and weight != '--' else None

        try:
            date_of_birth = str(datetime.datetime.strptime(dob, '%b %d, %Y').date()) if dob and dob != '--' else None
        except:
            date_of_birth = None

        def convert_height_to_cm(height: str) -> Optional[float]:
            try:
                ureg = pint.UnitRegistry()
                h_feet, h_inches = height.split(' ')
                h_feet = int(re.match(r'(\d+)', h_feet).group(0))
                h_inches = int(re.match(r'(\d+)', h_inches).group(0))
                total_height = h_feet * ureg.foot + h_inches * ureg.inch
                return round(total_height.to(ureg.centimeter).magnitude, 2)
            except:
                return None

        height_cm = convert_height_to_cm(height) if height and height != '--' else None

        def convert_reach_to_cm(reach: str) -> Optional[float]:
            return int(reach.split('"')[0]) * 2.54 if reach and reach != '--' else None

        reach_in_cm = convert_reach_to_cm(reach)

    except Exception as error:
        LOGGER.warning('Exception for %s on extract_bio_data(): %s', fighter_name, format_error(error))
        height_cm = weight_in_kg = reach_in_cm = stance = date_of_birth = None

    return {
        "height_cm": height_cm,
        "weight_in_kg": weight_in_kg,
        "reach_in_cm": reach_in_cm,
        "stance": stance if stance != '--' else None,
        "date_of_birth": date_of_birth
    }

def extract_career_data(soup: BeautifulSoup, fighter_name: str) -> dict:
    try:
        career_data = soup.select_one(
            '.b-list__info-box.b-list__info-box_style_middle-width')\
            .get_text(strip=True, separator='_').split('_')

        return {
            'significant_strikes_landed_per_minute': float(career_data[2]),
            'significant_striking_accuracy': float(career_data[4].replace('%', '')),
            'significant_strikes_absorbed_per_minute': float(career_data[6]),
            'significant_strike_defence': float(career_data[8].replace('%', '')),
            'average_takedowns_landed_per_15_minutes': float(career_data[10]),
            'takedown_accuracy': float(career_data[12].replace('%', '')),
            'takedown_defense': float(career_data[14].replace('%', '')),
            'average_submissions_attempted_per_15_minutes': float(career_data[2])
        }
    except Exception as error:
        LOGGER.warning('Exception for %s on extract_career_data(): %s', fighter_name, format_error(error))
        return {k: None for k in [
            'significant_strikes_landed_per_minute',
            'significant_striking_accuracy',
            'significant_strikes_absorbed_per_minute',
            'significant_strike_defence',
            'average_takedowns_landed_per_15_minutes',
            'takedown_accuracy',
            'takedown_defense',
            'average_submissions_attempted_per_15_minutes']}

def extract_fighter_data(fighter_html: str) -> dict:
    soup = BeautifulSoup(fighter_html, 'html.parser')

    try:
        fighter_name = soup.select_one('.b-content__title-highlight').get_text(strip=True)
    except AttributeError:
        fighter_name = None

    try:
        record_text = soup.select_one('.b-content__title-record').get_text(strip=True)
        win, loss, draw = map(int, record_text.split(' ')[-1].split('-'))
    except:
        win, loss, draw = None, None, None

    try:
        nickname = soup.select_one('.b-content__Nickname').get_text(strip=True) or None
    except:
        nickname = None

    bio_data = extract_bio_data(soup, fighter_name)
    career_data = extract_career_data(soup, fighter_name)

    return {
        "name": fighter_name,
        "nickname": nickname,
        "wins": win,
        "losses": loss,
        "draws": draw,
        **bio_data,
        **career_data
    }

# Substituir a parte de busca por isso:
def search_fighters(search_names):
    found = {}
    for name in search_names:
        letter = name.strip().split()[-1][0].upper()  # primeira letra do sobrenome
        LOGGER.info(f"Buscando na letra: {letter}")
        cached_links = get_cached_fighters(letter)
        if not cached_links:
            links = get_fighters_by_letter(letter)
            update_cache(letter, links)
        else:
            links = cached_links

        for link in links:
            try:
                page = basic_request(link, LOGGER)
                soup = BeautifulSoup(page, 'html.parser')
                fighter_name = soup.select_one('.b-content__title-highlight').get_text(strip=True).lower()

                if similar(fighter_name, name):
                    data = extract_fighter_data(page)
                    found[fighter_name] = data
                    LOGGER.info("Encontrado: %s", fighter_name)
                    break
            except Exception as e:
                LOGGER.warning("Erro ao processar link %s: %s", link, format_error(e))
    return found

def executor():
    global LOGGER
    _, data_folder, _, _, _ = setup_basic_file_paths(PROJECT_NAME)
    log_path = os.path.join(data_folder, 'Informacao_Lutadores.log')
    LOGGER = setup_logger(log_path)

    name_1 = input("Digite o nome do primeiro lutador: ").strip().lower()
    name_2 = input("Digite o nome do segundo lutador: ").strip().lower()
    search_names = {name_1, name_2}

    LOGGER.info("Buscando dados para: %s e %s", name_1, name_2)

    found = search_fighters(search_names)

    if len(found) < 2:
        LOGGER.warning("Nem todos os lutadores foram encontrados. Encontrados: %s", list(found.keys()))
    else:
        for fighter in found.values():
            LOGGER.info("Dados de %s:", fighter["name"])
            for k, v in fighter.items():
                LOGGER.info("  %s: %s", k, v)

        with open(os.path.join(data_folder, 'lutadores.json'), 'w', encoding='utf-8') as f:
            json.dump(list(found.values()), f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    executor()
