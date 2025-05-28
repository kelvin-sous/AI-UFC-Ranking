import string
import datetime
import re
import os
import logging
import time
from typing import Optional, Dict, List
import json

import numpy as np
from datetime import datetime
from typing import Tuple, Dict, Any
import math

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
            'significant_strikes_landed_per_minute': float(career_data[2]) if career_data[2] != '--' else None,
            'significant_striking_accuracy': float(career_data[4].replace('%', '')) if career_data[4] != '--' else None,
            'significant_strikes_absorbed_per_minute': float(career_data[6]) if career_data[6] != '--' else None,
            'significant_strike_defence': float(career_data[8].replace('%', '')) if career_data[8] != '--' else None,
            'average_takedowns_landed_per_15_minutes': float(career_data[10]) if career_data[10] != '--' else None,
            'takedown_accuracy': float(career_data[12].replace('%', '')) if career_data[12] != '--' else None,
            'takedown_defense': float(career_data[14].replace('%', '')) if career_data[14] != '--' else None,
            'average_submissions_attempted_per_15_minutes': float(career_data[16]) if len(career_data) > 16 and career_data[16] != '--' else None
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

def parse_stat_value(stat_text: str) -> Dict:
    """Parse estatísticas no formato 'X of Y' ou apenas 'X'"""
    try:
        stat_text = str(stat_text).strip()
        
        # Remover caracteres especiais e espaços extras
        stat_text = re.sub(r'\s+', ' ', stat_text)
        
        if ' of ' in stat_text:
            parts = stat_text.split(' of ')
            if len(parts) >= 2:
                landed = int(parts[0].strip())
                attempted = int(parts[1].strip())
                percentage = round((landed / attempted) * 100, 1) if attempted > 0 else 0
                return {
                    'landed': landed,
                    'attempted': attempted,
                    'percentage': percentage
                }
        elif stat_text.replace('-', '').isdigit():
            # Tratar casos como "5" ou "--" 
            if stat_text == '--' or stat_text == '---':
                return {'landed': 0, 'attempted': 0, 'percentage': 0}
            else:
                value = int(stat_text)
                return {
                    'landed': value,
                    'attempted': value,
                    'percentage': 100 if value > 0 else 0
                }
        else:
            # Tentar extrair números mesmo com formatação estranha
            numbers = re.findall(r'\d+', stat_text)
            if len(numbers) >= 2:
                landed = int(numbers[0])
                attempted = int(numbers[1])
                percentage = round((landed / attempted) * 100, 1) if attempted > 0 else 0
                return {
                    'landed': landed,
                    'attempted': attempted,
                    'percentage': percentage
                }
            elif len(numbers) == 1:
                value = int(numbers[0])
                return {
                    'landed': value,
                    'attempted': value,
                    'percentage': 100 if value > 0 else 0
                }
            
        return {'landed': 0, 'attempted': 0, 'percentage': 0}
        
    except Exception as e:
        LOGGER.warning(f"Erro ao fazer parse de estatística '{stat_text}': {format_error(e)}")
        return {'landed': 0, 'attempted': 0, 'percentage': 0}
    
def extract_round_number(text: str) -> int:
    """Extrai o número do round do texto do cabeçalho"""
    try:
        match = re.search(r'round (\d+)', text.lower())
        if match:
            return int(match.group(1))
        
        # Tentar outras formas de identificar o round
        if 'r1' in text.lower() or 'round 1' in text.lower():
            return 1
        elif 'r2' in text.lower() or 'round 2' in text.lower():
            return 2
        elif 'r3' in text.lower() or 'round 3' in text.lower():
            return 3
        elif 'r4' in text.lower() or 'round 4' in text.lower():
            return 4
        elif 'r5' in text.lower() or 'round 5' in text.lower():
            return 5
            
    except:
        pass
    
    return None
    
def process_significant_strikes_table(rows: list, our_fighter_index: int, fight_stats: dict):
    """Processa a tabela de significant strikes por alvo e posição"""
    try:
        if len(rows) >= 2:
            fighter_row = rows[0] if our_fighter_index == 0 else rows[1] if len(rows) > 1 else None
            opponent_row = rows[1] if our_fighter_index == 0 else rows[0]
            
            if fighter_row:
                cells = fighter_row.find_all('td')
                if len(cells) >= 8:
                    fight_stats['fighter_data']['significant_strikes_by_target'] = {
                        'head': parse_stat_value(cells[2].get_text(strip=True)),
                        'body': parse_stat_value(cells[3].get_text(strip=True)),
                        'leg': parse_stat_value(cells[4].get_text(strip=True))
                    }
                    
                    fight_stats['fighter_data']['significant_strikes_by_position'] = {
                        'distance': parse_stat_value(cells[5].get_text(strip=True)),
                        'clinch': parse_stat_value(cells[6].get_text(strip=True)),
                        'ground': parse_stat_value(cells[7].get_text(strip=True))
                    }
            
            if opponent_row:
                cells = opponent_row.find_all('td')
                if len(cells) >= 8:
                    fight_stats['opponent_data']['significant_strikes_by_target'] = {
                        'head': parse_stat_value(cells[2].get_text(strip=True)),
                        'body': parse_stat_value(cells[3].get_text(strip=True)),
                        'leg': parse_stat_value(cells[4].get_text(strip=True))
                    }
                    
                    fight_stats['opponent_data']['significant_strikes_by_position'] = {
                        'distance': parse_stat_value(cells[5].get_text(strip=True)),
                        'clinch': parse_stat_value(cells[6].get_text(strip=True)),
                        'ground': parse_stat_value(cells[7].get_text(strip=True))
                    }
    except Exception as e:
        LOGGER.warning(f"Erro ao processar tabela de significant strikes: {format_error(e)}")

def process_round_table(rows: list, our_fighter_index: int, fight_stats: dict, round_num: int):
    """Processa a tabela de estatísticas de um round específico"""
    try:
        if len(rows) >= 2:
            fighter_row = rows[0] if our_fighter_index == 0 else rows[1] if len(rows) > 1 else None
            opponent_row = rows[1] if our_fighter_index == 0 else rows[0]
            
            # Processar dados do nosso lutador
            if fighter_row:
                cells = fighter_row.find_all('td')
                if len(cells) >= 8:  # Verificar se tem todas as colunas necessárias
                    round_data = {
                        'round': round_num,
                        'significant_strikes': parse_stat_value(cells[1].get_text(strip=True)),
                        'significant_strikes_percentage': cells[2].get_text(strip=True).replace('%', ''),
                        'significant_strikes_by_target': {
                            'head': parse_stat_value(cells[3].get_text(strip=True)),
                            'body': parse_stat_value(cells[4].get_text(strip=True)),
                            'leg': parse_stat_value(cells[5].get_text(strip=True))
                        },
                        'significant_strikes_by_position': {
                            'distance': parse_stat_value(cells[6].get_text(strip=True)),
                            'clinch': parse_stat_value(cells[7].get_text(strip=True)),
                            'ground': parse_stat_value(cells[8].get_text(strip=True)) if len(cells) > 8 else {'landed': 0, 'attempted': 0, 'percentage': 0}
                        }
                    }
                    fight_stats['fighter_data']['round_by_round'].append(round_data)
            
            # Processar dados do oponente
            if opponent_row:
                cells = opponent_row.find_all('td')
                if len(cells) >= 8:
                    opponent_round_data = {
                        'round': round_num,
                        'significant_strikes': parse_stat_value(cells[1].get_text(strip=True)),
                        'significant_strikes_percentage': cells[2].get_text(strip=True).replace('%', ''),
                        'significant_strikes_by_target': {
                            'head': parse_stat_value(cells[3].get_text(strip=True)),
                            'body': parse_stat_value(cells[4].get_text(strip=True)),
                            'leg': parse_stat_value(cells[5].get_text(strip=True))
                        },
                        'significant_strikes_by_position': {
                            'distance': parse_stat_value(cells[6].get_text(strip=True)),
                            'clinch': parse_stat_value(cells[7].get_text(strip=True)),
                            'ground': parse_stat_value(cells[8].get_text(strip=True)) if len(cells) > 8 else {'landed': 0, 'attempted': 0, 'percentage': 0}
                        }
                    }
                    fight_stats['opponent_data']['round_by_round'].append(opponent_round_data)
                    
    except Exception as e:
        LOGGER.warning(f"Erro ao processar dados do round {round_num}: {format_error(e)}")
    
def process_total_stats_table(rows: list, our_fighter_index: int, fight_stats: dict):
    """Processa a tabela de estatísticas totais da luta"""
    try:
        if len(rows) >= 2:
            fighter_row = rows[0] if our_fighter_index == 0 else rows[1] if len(rows) > 1 else None
            opponent_row = rows[1] if our_fighter_index == 0 else rows[0]
            
            if fighter_row:
                cells = fighter_row.find_all('td')
                if len(cells) >= 9:
                    fight_stats['fighter_data']['total_stats'] = {
                        'knockdowns': int(cells[1].get_text(strip=True) or 0),
                        'significant_strikes': parse_stat_value(cells[2].get_text(strip=True)),
                        'total_strikes': parse_stat_value(cells[3].get_text(strip=True)),
                        'takedowns': parse_stat_value(cells[4].get_text(strip=True)),
                        'submission_attempts': int(cells[5].get_text(strip=True) or 0),
                        'reversals': int(cells[6].get_text(strip=True) or 0),
                        'control_time': cells[7].get_text(strip=True)
                    }
            
            if opponent_row:
                cells = opponent_row.find_all('td')
                if len(cells) >= 9:
                    fight_stats['opponent_data']['total_stats'] = {
                        'knockdowns': int(cells[1].get_text(strip=True) or 0),
                        'significant_strikes': parse_stat_value(cells[2].get_text(strip=True)),
                        'total_strikes': parse_stat_value(cells[3].get_text(strip=True)),
                        'takedowns': parse_stat_value(cells[4].get_text(strip=True)),
                        'submission_attempts': int(cells[5].get_text(strip=True) or 0),
                        'reversals': int(cells[6].get_text(strip=True) or 0),
                        'control_time': cells[7].get_text(strip=True)
                    }
    except Exception as e:
        LOGGER.warning(f"Erro ao processar tabela de estatísticas totais: {format_error(e)}")

def extract_fight_details(fight_url: str, fighter_name: str) -> Dict:
    """Extrai estatísticas detalhadas de uma luta específica com dados round-by-round"""
    try:
        LOGGER.info(f"Extraindo detalhes da luta: {fight_url}")
        fight_html = basic_request(fight_url, LOGGER)
        soup = BeautifulSoup(fight_html, 'html.parser')
        
        # Extrair informações do evento
        event_name = "Evento não encontrado"
        event_title = soup.select_one('.b-content__title-highlight')
        if event_title:
            event_name = event_title.get_text(strip=True)
        
        # Encontrar os nomes dos lutadores no cabeçalho da luta
        fighter_names = []
        person_names = soup.select('.b-fight-details__person-name a')
        for name_elem in person_names:
            name = name_elem.get_text(strip=True)
            if name:
                fighter_names.append(name)
        
        if len(fighter_names) < 2:
            LOGGER.warning(f"Não foi possível identificar os lutadores em {fight_url}")
            return {}
        
        fighter1_name = fighter_names[0]
        fighter2_name = fighter_names[1]
        
        # Determinar qual é nosso lutador (índice 0 ou 1)
        our_fighter_index = 0
        opponent_name = fighter2_name
        if similar(fighter2_name.lower(), fighter_name.lower()):
            our_fighter_index = 1
            opponent_name = fighter1_name
        
        fight_stats = {
            'event': event_name,
            'opponent': opponent_name,
            'fighter_data': {
                'name': fighter_name,
                'total_stats': {},
                'significant_strikes_by_target': {},
                'significant_strikes_by_position': {},
                'round_by_round': []
            },
            'opponent_data': {
                'name': opponent_name,
                'total_stats': {},
                'significant_strikes_by_target': {},
                'significant_strikes_by_position': {},
                'round_by_round': []
            }
        }
        
        # Buscar todas as seções de luta que contêm tabelas
        fight_sections = soup.find_all('section', class_='b-fight-details__section')
        
        LOGGER.info(f"Encontradas {len(fight_sections)} seções de luta")
        
        for section in fight_sections:
            # Buscar tabelas dentro da seção
            tables = section.find_all('table', class_='b-fight-details__table')
            
            for table in tables:
                tbody = table.find('tbody')
                if not tbody:
                    continue
                
                # Verificar se é uma tabela de round específico
                round_headers = tbody.find_all('thead', class_='b-fight-details__table-row_type_head')
                
                if round_headers:
                    # Esta é uma tabela com dados por round
                    current_round = None
                    rows_for_round = []
                    
                    for element in tbody.find_all(['thead', 'tr']):
                        if element.name == 'thead' and 'b-fight-details__table-row_type_head' in element.get('class', []):
                            # Processar round anterior se existir
                            if current_round is not None and len(rows_for_round) >= 2:
                                process_round_table(rows_for_round, our_fighter_index, fight_stats, current_round)
                            
                            # Extrair número do novo round
                            round_text = element.get_text(strip=True)
                            current_round = extract_round_number(round_text)
                            rows_for_round = []
                            
                        elif element.name == 'tr' and 'b-fight-details__table-row' in element.get('class', []):
                            # Verificar se não é um cabeçalho
                            if 'b-fight-details__table-row_type_head' not in element.get('class', []):
                                rows_for_round.append(element)
                    
                    # Processar último round
                    if current_round is not None and len(rows_for_round) >= 2:
                        process_round_table(rows_for_round, our_fighter_index, fight_stats, current_round)
                
                else:
                    # Tabelas de estatísticas gerais (sem rounds específicos)
                    rows = tbody.find_all('tr', class_='b-fight-details__table-row')
                    if len(rows) < 2:
                        continue
                    
                    # Identificar tipo da tabela pelo cabeçalho
                    thead = table.find('thead')
                    if thead:
                        header_cells = thead.find_all('th')
                        header_text = ' '.join([cell.get_text(strip=True) for cell in header_cells]).lower()
                        
                        # Tabela de estatísticas totais
                        if 'knockdowns' in header_text or 'total strikes' in header_text:
                            process_total_stats_table(rows, our_fighter_index, fight_stats)
                        
                        # Tabela de significant strikes por alvo/posição (geral da luta)
                        elif 'head' in header_text and 'body' in header_text and 'distance' in header_text:
                            process_significant_strikes_table(rows, our_fighter_index, fight_stats)
        
        # Ordenar rounds por número
        fight_stats['fighter_data']['round_by_round'].sort(key=lambda x: x.get('round', 0))
        fight_stats['opponent_data']['round_by_round'].sort(key=lambda x: x.get('round', 0))
        
        return fight_stats
        
    except Exception as e:
        LOGGER.warning(f"Erro ao extrair detalhes da luta {fight_url}: {format_error(e)}")
        return {}
    
def validate_fight_data(fight_data: dict) -> bool:
    """Valida se os dados da luta foram extraídos corretamente"""
    try:
        if not fight_data or not isinstance(fight_data, dict):
            return False
        
        # Verificar se existe dados básicos do lutador
        fighter_data = fight_data.get('fighter_data', {})
        if not fighter_data:
            return False
        
        # Verificar estatísticas totais
        total_stats = fighter_data.get('total_stats', {})
        if total_stats:
            sig_strikes = total_stats.get('significant_strikes', {})
            if sig_strikes.get('attempted', 0) > 0 or total_stats.get('knockdowns', 0) > 0:
                return True
        
        # Verificar se tem dados por alvo/posição
        by_target = fighter_data.get('significant_strikes_by_target', {})
        by_position = fighter_data.get('significant_strikes_by_position', {})
        if by_target or by_position:
            return True
        
        # Verificar se tem dados round-by-round
        rounds = fighter_data.get('round_by_round', [])
        if len(rounds) > 0:
            # Verificar se os rounds têm dados válidos
            for round_data in rounds:
                if round_data.get('significant_strikes', {}).get('attempted', 0) > 0:
                    return True
        
        return False
        
    except Exception as e:
        LOGGER.warning(f"Erro na validação dos dados da luta: {format_error(e)}")
        return False

def extract_fight_history(soup: BeautifulSoup, fighter_name: str) -> list[dict]:
    """Extrai histórico de lutas com estatísticas detalhadas"""
    history = []
    try:
        # Buscar tabela de histórico de lutas
        table = soup.select_one('.b-fight-details__table')
        if not table:
            table = soup.select_one('table.b-fight-details__table')
        
        rows = table.select('tr.b-fight-details__table-row') if table else []
        
        LOGGER.info(f"Encontradas {len(rows)} lutas para {fighter_name}")

        for i, row in enumerate(rows):
            cols = row.find_all('td')
            if len(cols) >= 6:
                # Extrair informações básicas
                result = cols[0].get_text(strip=True)
                
                # Extrair oponente e link da luta
                opponent_cell = cols[1]
                opponent_links = opponent_cell.find_all('a')
                
                opponent = ""
                fight_link = None
                
                for link in opponent_links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)
                    
                    if 'fight-details' in href:
                        fight_link = href
                    elif 'fighter-details' in href and text:
                        if not opponent or len(text) > len(opponent):
                            opponent = text
                
                # Se não encontrou oponente nos links, pegar todo o texto da célula
                if not opponent:
                    opponent = opponent_cell.get_text(strip=True)
                
                event = cols[2].get_text(strip=True)
                method = cols[3].get_text(strip=True)
                round_ = cols[4].get_text(strip=True)
                time_ = cols[5].get_text(strip=True)
                
                fight_data = {
                    "result": result,
                    "opponent": opponent,
                    "event": event,
                    "method": method,
                    "round": round_,
                    "time": time_,
                    "fight_link": fight_link,
                    "detailed_stats": {}
                }
                
                # Buscar estatísticas detalhadas se disponível
                if fight_link and result.lower() in ['win', 'loss', 'draw']:
                    LOGGER.info(f"Processando luta {i+1}/{len(rows)}: {fighter_name} vs {opponent}")
                    detailed_stats = extract_fight_details(fight_link, fighter_name)
                    
                    if validate_fight_data(detailed_stats):
                        fight_data['detailed_stats'] = detailed_stats
                        LOGGER.info(f"✓ Estatísticas extraídas com sucesso para {fighter_name} vs {opponent}")
                    else:
                        LOGGER.warning(f"✗ Falha na validação das estatísticas para {fighter_name} vs {opponent}")
                    
                    time.sleep(2)  # Delay menor para não sobrecarregar o servidor
                else:
                    if not fight_link:
                        LOGGER.debug(f"Link não encontrado para luta: {fighter_name} vs {opponent}")
                
                history.append(fight_data)
                
    except Exception as e:
        LOGGER.warning("Erro ao extrair histórico de lutas de %s: %s", fighter_name, format_error(e))

    return history

def extract_fighter_data(fighter_html: str) -> dict:
    soup = BeautifulSoup(fighter_html, 'html.parser')

    try:
        fighter_name = soup.select_one('.b-content__title-highlight').get_text(strip=True)
    except AttributeError:
        fighter_name = None

    try:
        record_text = soup.select_one('.b-content__title-record').get_text(strip=True)
        record_match = re.search(r'(\d+)-(\d+)-(\d+)', record_text)
        if record_match:
            win, loss, draw = map(int, record_match.groups())
        else:
            win, loss, draw = None, None, None
    except:
        win, loss, draw = None, None, None

    try:
        nickname_elem = soup.select_one('.b-content__Nickname')
        nickname = nickname_elem.get_text(strip=True) if nickname_elem else None
    except:
        nickname = None

    bio_data = extract_bio_data(soup, fighter_name)
    career_data = extract_career_data(soup, fighter_name)
    fight_history = extract_fight_history(soup, fighter_name)

    # Corrigir contagem manual se os dados estiverem faltando
    if win is None or loss is None or draw is None:
        win, loss, draw = count_fight_results(fight_history)

    return {
        "name": fighter_name,
        "nickname": nickname,
        "wins": win,
        "losses": loss,
        "draws": draw,
        **bio_data,
        **career_data,
        "fight_history": fight_history
    }

def search_fighters(search_names):
    found = {}

    for name in search_names:
        letter = name.strip().split()[-1][0].upper()
        LOGGER.info(f"Buscando na letra: {letter}")
        cached_links = get_cached_fighters(letter)
        if not cached_links:
            links = get_fighters_by_letter(letter)
            update_cache(letter, links)
        else:
            links = cached_links

        candidates = []
        for link in links:
            try:
                page = basic_request(link, LOGGER)
                soup = BeautifulSoup(page, 'html.parser')
                fighter_name_elem = soup.select_one('.b-content__title-highlight')
                if fighter_name_elem:
                    fighter_name = fighter_name_elem.get_text(strip=True).lower()
                    if similar(fighter_name, name):
                        candidates.append((fighter_name, page))
            except Exception as e:
                LOGGER.warning("Erro ao processar link %s: %s", link, format_error(e))

        if len(candidates) == 0:
            LOGGER.warning("Nenhum lutador encontrado com nome similar a '%s'.", name)
            continue
        elif len(candidates) == 1:
            selected_name, selected_page = candidates[0]
        else:
            print(f"\nForam encontrados múltiplos lutadores semelhantes a '{name}':")
            for idx, (fighter_name, _) in enumerate(candidates, start=1):
                print(f"{idx}. {fighter_name}")
            while True:
                try:
                    choice = int(input(f"Escolha o número correspondente ao lutador desejado (1-{len(candidates)}): "))
                    if 1 <= choice <= len(candidates):
                        selected_name, selected_page = candidates[choice - 1]
                        break
                    else:
                        print("Escolha inválida. Tente novamente.")
                except ValueError:
                    print("Entrada inválida. Digite um número.")

        data = extract_fighter_data(selected_page)
        found[name.lower()] = data

    return found

def count_fight_results(fight_history: list[dict]) -> tuple[int, int, int]:
    wins = losses = draws = 0
    for fight in fight_history:
        result = fight.get("result", "").lower()
        if result == "win":
            wins += 1
        elif result == "loss":
            losses += 1
        elif result == "draw":
            draws += 1
    return wins, losses, draws

def calculate_age(date_of_birth: str) -> int:
    """Calcula a idade do lutador com base na data de nascimento"""
    if not date_of_birth:
        return 30  # Idade média padrão
    try:
        birth_date = datetime.strptime(date_of_birth, '%Y-%m-%d')
        today = datetime.now()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except:
        return 30
    
def calculate_recent_form(fight_history: list, last_n_fights: int = 5) -> float:
    """Calcula a forma recente do lutador baseada nas últimas N lutas"""
    recent_fights = [f for f in fight_history if f.get('result', '').lower() in ['win', 'loss', 'draw']][:last_n_fights]
    
    if not recent_fights:
        return 0.5
    
    wins = sum(1 for f in recent_fights if f.get('result', '').lower() == 'win')
    return wins / len(recent_fights)

def calculate_finish_rate(fight_history: list) -> Tuple[float, float]:
    """Calcula taxa de finalização (KO/TKO/SUB) e taxa de ser finalizado"""
    completed_fights = [f for f in fight_history if f.get('result', '').lower() in ['win', 'loss']]
    
    if not completed_fights:
        return 0.0, 0.0
    
    wins = [f for f in completed_fights if f.get('result', '').lower() == 'win']
    losses = [f for f in completed_fights if f.get('result', '').lower() == 'loss']
    
    # Taxa de finalização (wins por KO/TKO/SUB)
    finish_wins = sum(1 for f in wins if any(method in f.get('method', '').lower() 
                                           for method in ['ko', 'tko', 'submission', 'sub']))
    finish_rate = finish_wins / len(wins) if wins else 0.0
    
    # Taxa de ser finalizado
    finished_losses = sum(1 for f in losses if any(method in f.get('method', '').lower() 
                                                 for method in ['ko', 'tko', 'submission', 'sub']))
    finished_rate = finished_losses / len(losses) if losses else 0.0
    
    return finish_rate, finished_rate

def calculate_experience_score(fight_history: list) -> float:
    """Calcula pontuação de experiência baseada no número e qualidade das lutas"""
    completed_fights = [f for f in fight_history if f.get('result', '').lower() in ['win', 'loss', 'draw']]
    
    if not completed_fights:
        return 0.0
    
    # Experiência base pelo número de lutas
    base_experience = min(len(completed_fights) / 20.0, 1.0)  # Normalizado para máximo 20 lutas
    
    # Bônus por lutas de título ou eventos principais (heurística baseada no nome do evento)
    title_fights = sum(1 for f in completed_fights if any(keyword in f.get('event', '').lower() 
                                                        for keyword in ['title', 'championship', 'main event']))
    title_bonus = min(title_fights * 0.1, 0.3)
    
    return min(base_experience + title_bonus, 1.0)

def calculate_physical_advantages(fighter1: dict, fighter2: dict) -> Tuple[float, float]:
    """Calcula vantagens físicas entre os lutadores (reach, altura, peso)"""
    def safe_get(data: dict, key: str, default: float = 0.0) -> float:
        value = data.get(key)
        return float(value) if value is not None else default
    
    # Vantagem de reach
    reach1 = safe_get(fighter1, 'reach_in_cm', 175.0)
    reach2 = safe_get(fighter2, 'reach_in_cm', 175.0)
    reach_advantage1 = max(0, (reach1 - reach2) / 10.0)  # Normalizado por 10cm
    reach_advantage2 = max(0, (reach2 - reach1) / 10.0)
    
    # Vantagem de altura
    height1 = safe_get(fighter1, 'height_cm', 175.0)
    height2 = safe_get(fighter2, 'height_cm', 175.0)
    height_advantage1 = max(0, (height1 - height2) / 10.0)  # Normalizado por 10cm
    height_advantage2 = max(0, (height2 - height1) / 10.0)
    
    # Combinar vantagens físicas
    physical_advantage1 = min((reach_advantage1 + height_advantage1) / 2.0, 1.0)
    physical_advantage2 = min((reach_advantage2 + height_advantage2) / 2.0, 1.0)
    
    return physical_advantage1, physical_advantage2

def extract_striking_metrics(fighter: dict) -> dict:
    """Extrai e normaliza métricas de striking"""
    def safe_get(value, default=0.0):
        return float(value) if value is not None else default
    
    return {
        'striking_rate': safe_get(fighter.get('significant_strikes_landed_per_minute'), 3.0),
        'striking_accuracy': safe_get(fighter.get('significant_striking_accuracy'), 45.0) / 100.0,
        'striking_defense': safe_get(fighter.get('significant_strike_defence'), 55.0) / 100.0,
        'striking_absorbed': safe_get(fighter.get('significant_strikes_absorbed_per_minute'), 3.0)
    }

def extract_grappling_metrics(fighter: dict) -> dict:
    """Extrai e normaliza métricas de grappling"""
    def safe_get(value, default=0.0):
        return float(value) if value is not None else default
    
    return {
        'takedown_rate': safe_get(fighter.get('average_takedowns_landed_per_15_minutes'), 1.0),
        'takedown_accuracy': safe_get(fighter.get('takedown_accuracy'), 40.0) / 100.0,
        'takedown_defense': safe_get(fighter.get('takedown_defense'), 70.0) / 100.0,
        'submission_rate': safe_get(fighter.get('average_submissions_attempted_per_15_minutes'), 0.5)
    }

def calculate_striking_advantage(fighter1: dict, fighter2: dict) -> float:
    """Calcula vantagem no striking entre os lutadores"""
    striking1 = extract_striking_metrics(fighter1)
    striking2 = extract_striking_metrics(fighter2)
    
    # Componentes da vantagem no striking
    output_advantage = (striking1['striking_rate'] - striking2['striking_rate']) / 5.0  # Normalizado por 5 strikes/min
    accuracy_advantage = striking1['striking_accuracy'] - striking2['striking_accuracy']
    defense_advantage = striking1['striking_defense'] - striking2['striking_defense']
    durability_advantage = (striking2['striking_absorbed'] - striking1['striking_absorbed']) / 3.0  # Menos absorção é melhor
    
    # Média ponderada
    striking_advantage = (output_advantage * 0.3 + accuracy_advantage * 0.25 + 
                         defense_advantage * 0.25 + durability_advantage * 0.2)
    
    return max(-1.0, min(1.0, striking_advantage))  # Limitado entre -1 e 1

def calculate_grappling_advantage(fighter1: dict, fighter2: dict) -> float:
    """Calcula vantagem no grappling entre os lutadores"""
    grappling1 = extract_grappling_metrics(fighter1)
    grappling2 = extract_grappling_metrics(fighter2)
    
    # Componentes da vantagem no grappling
    takedown_output = (grappling1['takedown_rate'] - grappling2['takedown_rate']) / 3.0  # Normalizado por 3 TD/15min
    takedown_efficiency = grappling1['takedown_accuracy'] - grappling2['takedown_accuracy']
    takedown_defense_diff = grappling1['takedown_defense'] - grappling2['takedown_defense']
    submission_threat = (grappling1['submission_rate'] - grappling2['submission_rate']) / 2.0  # Normalizado por 2 SUB/15min
    
    # Média ponderada
    grappling_advantage = (takedown_output * 0.3 + takedown_efficiency * 0.25 + 
                          takedown_defense_diff * 0.25 + submission_threat * 0.2)
    
    return max(-1.0, min(1.0, grappling_advantage))  # Limitado entre -1 e 1

def calculate_fighter_score(fighter: dict) -> dict:
    """Calcula pontuações abrangentes do lutador"""
    # Métricas básicas
    wins = fighter.get('wins', 0)
    losses = fighter.get('losses', 0)
    total_fights = wins + losses + fighter.get('draws', 0)
    
    win_rate = wins / total_fights if total_fights > 0 else 0.5
    age = calculate_age(fighter.get('date_of_birth'))
    
    # Forma recente e experiência
    recent_form = calculate_recent_form(fighter.get('fight_history', []))
    experience = calculate_experience_score(fighter.get('fight_history', []))
    
    # Taxas de finalização
    finish_rate, finished_rate = calculate_finish_rate(fighter.get('fight_history', []))
    
    # Fator idade (pico entre 25-30 anos)
    age_factor = 1.0 - abs(27.5 - age) / 20.0  # Penaliza idades muito distantes de 27.5
    age_factor = max(0.5, min(1.0, age_factor))
    
    return {
        'win_rate': win_rate,
        'recent_form': recent_form,
        'experience': experience,
        'finish_rate': finish_rate,
        'durability': 1.0 - finished_rate,  # Inverso da taxa de ser finalizado
        'age_factor': age_factor,
        'total_fights': total_fights
    }

def predict_fight_outcome(fighter1_data: dict, fighter2_data: dict) -> dict:
    """Função principal para prever o resultado da luta"""
    
    # Calcular pontuações individuais
    f1_scores = calculate_fighter_score(fighter1_data)
    f2_scores = calculate_fighter_score(fighter2_data)
    
    # Calcular vantagens técnicas
    striking_advantage = calculate_striking_advantage(fighter1_data, fighter2_data)
    grappling_advantage = calculate_grappling_advantage(fighter1_data, fighter2_data)
    
    # Calcular vantagens físicas
    physical_adv_f1, physical_adv_f2 = calculate_physical_advantages(fighter1_data, fighter2_data)
    
    # === ALGORITMO DE PREDIÇÃO ===
    
    # Componente 1: Habilidade geral (40% do peso)
    skill_component = (
        (f1_scores['win_rate'] - f2_scores['win_rate']) * 0.3 +
        (f1_scores['recent_form'] - f2_scores['recent_form']) * 0.4 +
        (f1_scores['experience'] - f2_scores['experience']) * 0.3
    ) * 0.4
    
    # Componente 2: Vantagens técnicas (35% do peso)
    technical_component = (striking_advantage * 0.6 + grappling_advantage * 0.4) * 0.35
    
    # Componente 3: Fatores físicos e de durabilidade (25% do peso)
    physical_component = (
        (physical_adv_f1 - physical_adv_f2) * 0.4 +
        (f1_scores['durability'] - f2_scores['durability']) * 0.3 +
        (f1_scores['age_factor'] - f2_scores['age_factor']) * 0.2 +
        (f1_scores['finish_rate'] - f2_scores['finish_rate']) * 0.1
    ) * 0.25
    
    # Pontuação final
    final_score = skill_component + technical_component + physical_component
    
    # Converter para probabilidade usando função sigmoid
    probability_f1 = 1 / (1 + math.exp(-final_score * 5))  # Multiplicar por 5 para aumentar a sensibilidade
    probability_f2 = 1 - probability_f1
    
    # Ajustar para evitar predições muito extremas
    if probability_f1 > 0.85:
        probability_f1 = 0.85
        probability_f2 = 0.15
    elif probability_f1 < 0.15:
        probability_f1 = 0.15
        probability_f2 = 0.85
    
    return {
        'fighter1': {
            'name': fighter1_data.get('name', 'Fighter 1'),
            'win_probability': round(probability_f1, 3),
            'scores': f1_scores
        },
        'fighter2': {
            'name': fighter2_data.get('name', 'Fighter 2'),
            'win_probability': round(probability_f2, 3),
            'scores': f2_scores
        },
        'analysis': {
            'striking_advantage_f1': round(striking_advantage, 3),
            'grappling_advantage_f1': round(grappling_advantage, 3),
            'physical_advantage_f1': round(physical_adv_f1 - physical_adv_f2, 3),
            'skill_component': round(skill_component, 3),
            'technical_component': round(technical_component, 3),
            'physical_component': round(physical_component, 3),
            'final_score': round(final_score, 3)
        },
        'prediction': {
            'winner': fighter1_data.get('name') if probability_f1 > probability_f2 else fighter2_data.get('name'),
            'confidence': round(max(probability_f1, probability_f2), 3),
            'method_prediction': predict_finish_method(fighter1_data, fighter2_data, probability_f1)
        }
    }

def predict_finish_method(fighter1: dict, fighter2: dict, f1_win_prob: float) -> str:
    """Prediz o método mais provável de finalização"""
    f1_finish, _ = calculate_finish_rate(fighter1.get('fight_history', []))
    f2_finish, _ = calculate_finish_rate(fighter2.get('fight_history', []))
    
    striking1 = extract_striking_metrics(fighter1)
    striking2 = extract_striking_metrics(fighter2)
    grappling1 = extract_grappling_metrics(fighter1)
    grappling2 = extract_grappling_metrics(fighter2)
    
    if f1_win_prob > 0.5:
        # Fighter 1 favorito
        if f1_finish > 0.4:
            if striking1['striking_rate'] > grappling1['submission_rate'] * 5:
                return "KO/TKO"
            else:
                return "Submission"
        else:
            return "Decision"
    else:
        # Fighter 2 favorito
        if f2_finish > 0.4:
            if striking2['striking_rate'] > grappling2['submission_rate'] * 5:
                return "KO/TKO"
            else:
                return "Submission"
        else:
            return "Decision"
        
def display_prediction_results(prediction: dict):
    """Exibe os resultados da predição de forma organizada"""
    print("\n" + "="*80)
    print("🥊 PREDIÇÃO DE LUTA UFC 🥊")
    print("="*80)
    
    f1 = prediction['fighter1']
    f2 = prediction['fighter2']
    
    print(f"\n🔴 {f1['name']}")
    print(f"   Probabilidade de vitória: {f1['win_probability']*100:.1f}%")
    print(f"   Taxa de vitórias: {f1['scores']['win_rate']*100:.1f}%")
    print(f"   Forma recente: {f1['scores']['recent_form']*100:.1f}%")
    print(f"   Experiência: {f1['scores']['experience']*100:.1f}%")
    
    print(f"\n🔵 {f2['name']}")
    print(f"   Probabilidade de vitória: {f2['win_probability']*100:.1f}%")
    print(f"   Taxa de vitórias: {f2['scores']['win_rate']*100:.1f}%")
    print(f"   Forma recente: {f2['scores']['recent_form']*100:.1f}%")
    print(f"   Experiência: {f2['scores']['experience']*100:.1f}%")
    
    print(f"\n🏆 PREDIÇÃO FINAL:")
    print(f"   Vencedor previsto: {prediction['prediction']['winner']}")
    print(f"   Confiança: {prediction['prediction']['confidence']*100:.1f}%")
    print(f"   Método previsto: {prediction['prediction']['method_prediction']}")
    
    print(f"\n📊 ANÁLISE TÉCNICA:")
    analysis = prediction['analysis']
    print(f"   Vantagem no Striking: {analysis['striking_advantage_f1']:+.3f}")
    print(f"   Vantagem no Grappling: {analysis['grappling_advantage_f1']:+.3f}")
    print(f"   Vantagem Física: {analysis['physical_advantage_f1']:+.3f}")
    
    print("="*80)

def executor():
    """Versão modificada do executor que inclui predição"""
    fighter1_name = input("Digite o nome do primeiro lutador: ").strip().lower()
    fighter2_name = input("Digite o nome do segundo lutador: ").strip().lower()

    LOGGER.info("Buscando dados para: %s e %s", fighter1_name, fighter2_name)

    results = search_fighters([fighter1_name, fighter2_name])
    fighter1 = results.get(fighter1_name)
    fighter2 = results.get(fighter2_name)

    if not fighter1 or not fighter2:
        LOGGER.error("Um dos lutadores não foi encontrado.")
        return

    # Salvar JSONs separados (código original)
    safe_name1 = re.sub(r'[^\w\s-]', '', fighter1['name']).strip().replace(' ', '_')
    safe_name2 = re.sub(r'[^\w\s-]', '', fighter2['name']).strip().replace(' ', '_')
    
    filename1 = f"{safe_name1}_complete_stats.json"
    filename2 = f"{safe_name2}_complete_stats.json"
    
    with open(filename1, "w", encoding="utf-8") as f:
        json.dump(fighter1, f, ensure_ascii=False, indent=2)
    
    with open(filename2, "w", encoding="utf-8") as f:
        json.dump(fighter2, f, ensure_ascii=False, indent=2)

    LOGGER.info("Arquivos salvos: %s e %s", filename1, filename2)
    
    # === NOVA FUNCIONALIDADE: PREDIÇÃO ===
    print("\n🤖 Executando análise preditiva...")
    prediction = predict_fight_outcome(fighter1, fighter2)
    
    # Salvar predição
    prediction_filename = f"prediction_{safe_name1}_vs_{safe_name2}.json"
    with open(prediction_filename, "w", encoding="utf-8") as f:
        json.dump(prediction, f, ensure_ascii=False, indent=2)
    
    # Exibir resultados
    display_prediction_results(prediction)
    
    LOGGER.info("Predição salva em: %s", prediction_filename)
    
    # Log de resumo das lutas (código original mantido)
    for fighter in [fighter1, fighter2]:
        LOGGER.info("=" * 80)
        LOGGER.info("DADOS COMPLETOS - %s", fighter["name"])
        LOGGER.info("Record: %d-%d-%d", fighter.get("wins", 0), fighter.get("losses", 0), fighter.get("draws", 0))
        
        completed_fights = [f for f in fighter['fight_history'] if f.get('result', '').lower() in ['win', 'loss', 'draw']]
        LOGGER.info("Total de lutas UFC: %d", len(completed_fights))
        
        # Mostrar resumo das lutas com estatísticas
        for fight in completed_fights[:3]:  # Mostrar apenas as 3 primeiras
            if fight.get('detailed_stats'):
                stats = fight['detailed_stats']
                LOGGER.info("Luta vs %s: %s", fight['opponent'], fight['result'])
                if 'fighter_data' in stats:
                    total_stats = stats['fighter_data'].get('total_stats', {})
                    sig_strikes = total_stats.get('significant_strikes', {})
                    LOGGER.info("  Sig. Strikes: %d/%d", sig_strikes.get('landed', 0), sig_strikes.get('attempted', 0))

if __name__ == "__main__":
    try:
        # Setup inicial
        base_folder, data_folder, raw_folder, interim_folder, log_file_path = setup_basic_file_paths(PROJECT_NAME)
        LOGGER = setup_logger(log_file_path)
        
        LOGGER.info("Iniciando scraper UFC Stats")
        LOGGER.info("Pastas criadas: %s, %s, %s", raw_folder, interim_folder, log_file_path)
        
        # Executar o programa principal
        executor()
        
    except KeyboardInterrupt:
        if LOGGER:
            LOGGER.info("Programa interrompido pelo usuário")
    except Exception as e:
        if LOGGER:
            LOGGER.error("Erro fatal: %s", format_error(e))
        else:
            print(f"Erro fatal: {format_error(e)}")
    finally:
        if LOGGER:
            LOGGER.info("Programa finalizado")