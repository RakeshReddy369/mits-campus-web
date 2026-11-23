#!/usr/bin/env python3
"""
Web Campus - GEMS API Client & Attendance Intelligence Engine
Supports live GEMS MITS IMS integration as well as mock demo simulation.
Uses standard library urllib (with requests fallback if available) for zero external dependencies.
"""

import json
import re
import ast
import unicodedata
import math
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.parse
import http.cookiejar
import gzip
import io

# Optional requests fallback
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def has_control_characters(text):
    """Check if text contains problematic control characters."""
    for char in text:
        code = ord(char)
        if code < 32 and char not in '\t\n\r':
            return True
    return False


def sanitize_json_text(text):
    """
    Remove control characters that break JSON parsing.
    Cleans ASCII control characters (0x00-0x1F) except tab, newline, carriage return.
    """
    cleaned = []
    for char in text:
        code = ord(char)
        if code >= 32 or char in '\t\n\r':
            cleaned.append(char)
        else:
            cleaned.append(' ')
    return ''.join(cleaned)


def time_to_24h(t):
    """
    Convert GEMS '9:00 AM' style times to 'HH:MM:SS'.
    GEMS labels the 12 noon slot as '12:00 AM', so hour 0 is treated as noon.
    """
    t = str(t).strip()
    for fmt in ('%I:%M %p', '%I:%M%p'):
        try:
            parsed = datetime.strptime(t.upper(), fmt)
            if parsed.hour == 0:
                parsed = parsed.replace(hour=12)
            return parsed.strftime('%H:%M:%S')
        except ValueError:
            continue
    return t


def parse_timetable_js_text(text):
    """
    Robust parser for malformed JS-style timetable response from GEMS.
    Extracts each day block and pulls out quoted fields directly.
    """
    compact = re.sub(r'\s+', ' ', text)
    day_pat = re.compile(
        r"['\"]?(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)['\"]?\s*:\s*\[(.*?)\]",
        re.IGNORECASE | re.DOTALL,
    )
    field_pat = re.compile(r"([A-Za-z_]\w*)\s*[:=]\s*(?:'([^']*)'|\"([^\"]*)\")")

    result = {}
    for m in day_pat.finditer(compact):
        day = m.group(1).capitalize()
        classes = []
        for obj in re.finditer(r'\{(.*?)\}', m.group(2)):
            fields = {}
            for f in field_pat.finditer(obj.group(1)):
                value = f.group(2) if f.group(2) is not None else f.group(3)
                fields[f.group(1)] = value.strip() if isinstance(value, str) else value
            if not fields:
                continue
            if 'time' in fields:
                fields['time'] = time_to_24h(fields['time'])
            if 'RoomNo' in fields and 'roomNo' not in fields:
                fields['roomNo'] = fields.pop('RoomNo')
            classes.append(fields)
        result[day] = classes

    return result or None


def calculate_attendance_insights(attended, conducted, target_pct=75.0):
    """
    Calculates key metrics for a subject:
    - Current percentage
    - Safe cuts / bunk margin (classes student can miss without dropping below target)
    - Required classes (consecutive classes needed to attend to reach target)
    """
    if conducted == 0:
        return {
            'percentage': 0.0,
            'status': 'No Classes',
            'safeBunks': 0,
            'classesNeeded': 0,
            'badgeColor': 'gray',
            'message': 'No classes conducted yet.'
        }

    pct = round((attended / conducted) * 100, 2)
    t = target_pct / 100.0

    if pct >= target_pct:
        safe_bunks = int(math.floor((attended - (t * conducted)) / t))
        safe_bunks = max(0, safe_bunks)
        badge_color = 'green' if pct >= 80 else 'teal'
        msg = f"You can safely miss {safe_bunks} more class{'es' if safe_bunks != 1 else ''} and stay ≥{int(target_pct)}%." if safe_bunks > 0 else f"On the edge! Don't miss the next class to stay ≥{int(target_pct)}%."
        classes_needed = 0
    else:
        if t >= 1.0:
            classes_needed = 999
        else:
            classes_needed = int(math.ceil(((t * conducted) - attended) / (1.0 - t)))
        classes_needed = max(1, classes_needed)
        safe_bunks = 0
        badge_color = 'orange' if pct >= 65 else 'red'
        msg = f"Need to attend next {classes_needed} consecutive class{'es' if classes_needed != 1 else ''} to reach {int(target_pct)}%."

    return {
        'percentage': pct,
        'safeBunks': safe_bunks,
        'classesNeeded': classes_needed,
        'badgeColor': badge_color,
        'message': msg
    }


class GemsSession:
    """Session handler that uses urllib or requests transparently."""
    def __init__(self):
        self.headers = {
            'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 15; SM-G990B2 Build/AP3A.240905.015.A2)',
            'Connection': 'Keep-Alive',
            'Accept-Encoding': 'gzip',
            'Accept-Charset': 'UTF-8'
        }
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def get(self, url, params=None, timeout=15):
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}" if '?' not in url else f"{url}&{query}"

        req = urllib.request.Request(url, headers=self.headers)
        response = self.opener.open(req, timeout=timeout)
        content = response.read()

        if response.headers.get('Content-Encoding') == 'gzip' or (len(content) > 2 and content[:2] == b'\x1f\x8b'):
            try:
                buf = io.BytesIO(content)
                with gzip.GzipFile(fileobj=buf) as f:
                    content = f.read()
            except Exception:
                pass

        charset = response.headers.get_content_charset() or 'utf-8'
        text = content.decode(charset, errors='replace')

        class ResponseWrapper:
            def __init__(self, status, text):
                self.status_code = status
                self.text = text

        return ResponseWrapper(response.status, text)


class WebCampusGemsClient:
    """
    The GEMS API Client for Web Campus.
    Connects to MITS IMS portal, parses attendance, schedules, and generates student intelligence.
    """
    def __init__(self):
        self.base_url = "http://mitsims.in"
        self.session = GemsSession()

    def login(self, username, password):
        """Login using GEMS API endpoint."""
        url = f"{self.base_url.rstrip('/')}/studentAppLogin/studentLogin.action"
        username = str(username).strip()
        password = str(password).strip()

        params = {
            'actionType': 'studentAppLogin',
            'personType': 'student',
            'userId': username,
            'password': password
        }

        try:
            res = self.session.get(url, params=params, timeout=15)
            if res.status_code == 200:
                text = res.text
                text = re.sub(r'(\w+)\s*:', r'"\1":', text)
                text = text.replace("'", '"')

                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    if 'control character' in str(e).lower():
                        text = sanitize_json_text(text)
                        data = json.loads(text)
                    else:
                        raise

                if data.get('status') == 'success':
                    return data['studentLoginDetails'][0]
                else:
                    return None
            return None
        except Exception as e:
            print(f"[WebCampus Client] Login error: {e}")
            return None

    def get_attendance(self, student_details):
        """Fetch subject-wise attendance from GEMS."""
        student_id = student_details['id']
        auth_token = student_details['authToken']
        url = f"{self.base_url.rstrip('/')}/studentApp/getAttendanceDetails.action"

        params = {
            'tkn': auth_token,
            'stdnt.id': student_id,
            'studentId': student_id,
            'actionType': 'attendanceDetails',
            'studentType': 'student'
        }

        try:
            res = self.session.get(url, params=params, timeout=15)
            if res.status_code == 200:
                text = res.text
                text = re.sub(r'(\w+):', r'"\1":', text)
                text = text.replace("'", '"')

                try:
                    data = json.loads(text, strict=False)
                except json.JSONDecodeError as e:
                    if 'control character' in str(e).lower():
                        text = sanitize_json_text(text)
                        data = json.loads(text, strict=False)
                    else:
                        raise

                if data.get('status') == 'success':
                    return data.get('attendanceDetails', [])
            return None
        except Exception as e:
            print(f"[WebCampus Client] Attendance error: {e}")
            return None

    def get_todays_classes(self, student_details):
        """Fetch today's scheduled classes and real-time attendance marks."""
        student_id = student_details['id']
        auth_token = student_details['authToken']
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        date_formats = [now_ist.strftime('%d/%m/%Y'), now_ist.strftime('%d/%m/%y')]
        url = f"{self.base_url.rstrip('/')}/studentApp/dashboard.action"

        for date_str in date_formats:
            params = {
                'tkn': auth_token,
                'stdnt.id': student_id,
                'studentId': student_id,
                'studentType': 'student',
                'dateString': date_str
            }
            try:
                res = self.session.get(url, params=params, timeout=15)
                if res.status_code == 200:
                    text = res.text
                    text = re.sub(r'\s+', ' ', text)
                    text = text.replace("'", '"')
                    text = re.sub(r'"(\d{2}):(\d{2}):(\d{2})"', r'"\1|\2|\3"', text)
                    text = re.sub(r'(\w+)\s*:', r'"\1":', text)
                    text = text.replace('|', ':')

                    try:
                        data = json.loads(text)
                    except Exception:
                        text = sanitize_json_text(text)
                        try:
                            data = json.loads(text)
                        except Exception:
                            continue

                    classes_raw = []
                    if isinstance(data, dict):
                        classes_raw = (
                            data.get('classDetails') or data.get('todayClasses') or
                            data.get('classes') or data.get('timeTableDetails') or
                            data.get('studentClassDetails') or data.get('grid') or
                            data.get('data') or []
                        )
                    elif isinstance(data, list):
                        classes_raw = data

                    if classes_raw:
                        normalized = []
                        for cls in classes_raw:
                            if not isinstance(cls, dict):
                                continue
                            subj = cls.get('subjectName') or cls.get('subject') or cls.get('courseName') or cls.get('title') or ''
                            code = cls.get('code') or cls.get('subjectCode') or cls.get('courseCode') or ''
                            fac = cls.get('facultyName') or cls.get('faculty') or cls.get('employeeName') or cls.get('teacherName') or ''
                            time_val = cls.get('time') or cls.get('startTime') or cls.get('classTime') or '00:00:00'
                            room_val = cls.get('roomNo') or cls.get('room') or cls.get('roomName') or ''

                            raw_st = str(cls.get('status') or cls.get('attStatus') or cls.get('attendanceStatus') or '').strip().lower()
                            if raw_st in ['present', 'p', 'p*', 'y', '1', 'attended']:
                                status = 'Present'
                            elif raw_st in ['permission', 'leave']:
                                status = 'Permission'
                            elif raw_st in ['absent', 'a', 'a*', 'n', '0', 'bunked']:
                                status = 'Absent'
                            else:
                                status = 'Not Updated'

                            subj_clean = str(subj).strip().upper()
                            if not subj_clean or subj_clean in ['FREE', 'NO CLASS', 'LUNCH', 'BREAK', 'LIBRARY', '-', 'UNKNOWN']:
                                continue

                            normalized.append({
                                'subjectName': subj,
                                'code': code,
                                'facultyName': fac,
                                'time': time_to_24h(time_val),
                                'roomNo': room_val,
                                'status': status,
                                'attPercentage': cls.get('attPercentage', cls.get('percentage', None))
                            })
                        if normalized:
                            return normalized
            except Exception as e:
                print(f"[WebCampus Client] Today's classes error: {e}")
                continue
        return []

    def get_timetable(self, student_details):
        """Fetch weekly timetable."""
        student_id = student_details['id']
        auth_token = student_details['authToken']
        url = f"{self.base_url.rstrip('/')}/studentApp/getAttendanceDetails.action"

        params = {
            'tkn': auth_token,
            'stdnt.id': student_id,
            'studentId': student_id,
            'actionType': 'timeTableDetails',
            'studentType': 'student'
        }

        try:
            res = self.session.get(url, params=params, timeout=15)
            if res.status_code == 200:
                raw_text = res.text
                text = re.sub(r"(\w+)='", r"\1:'", raw_text)
                text = re.sub(r'(\w+)="', r'\1:"', text)
                text = re.sub(r'(\w+)\s*:', r'\1:', text)

                data = None
                try:
                    data = ast.literal_eval(text)
                except Exception:
                    text = re.sub(r'(\w+):', r'"\1":', text)
                    text = text.replace("'", '"')
                    try:
                        data = json.loads(text)
                    except Exception:
                        data = None

                timetable = None
                if isinstance(data, dict):
                    timetable = (
                        data.get('TimeTableDetails') or data.get('timeTableDetails') or
                        data.get('timetableDetails') or data.get('timeTable') or
                        data.get('grid') or data.get('data')
                    )

                if not timetable:
                    timetable = parse_timetable_js_text(raw_text)

                return timetable
            return None
        except Exception as e:
            print(f"[WebCampus Client] Timetable error: {e}")
            return None

    def _normalize_timetable(self, timetable_raw):
        """Convert timetable into { Monday: [...], Tuesday: [...] }."""
        if not timetable_raw:
            return {}

        day_names = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
        day_map = {}

        if isinstance(timetable_raw, dict):
            for k, v in timetable_raw.items():
                if str(k).strip().lower() in day_names and isinstance(v, list):
                    day_map[str(k).strip().capitalize()] = v
        elif isinstance(timetable_raw, list):
            for item in timetable_raw:
                if not isinstance(item, dict):
                    continue
                day = str(item.get('day', item.get('dayName', ''))).strip()
                if day:
                    classes = item.get('classDetails') or item.get('classes') or item.get('periods') or []
                    day_map[day.capitalize()] = classes
                else:
                    for k, v in item.items():
                        if str(k).strip().lower() in day_names and isinstance(v, list):
                            day_map[str(k).strip().capitalize()] = v

        normalized = {}
        for day, classes_list in day_map.items():
            parsed = []
            for cls in classes_list:
                if not isinstance(cls, dict):
                    continue
                subj = cls.get('subjectName') or cls.get('subject') or cls.get('courseName') or ''
                subj_clean = str(subj).strip().upper()
                if not subj_clean or subj_clean in ['FREE', 'NO CLASS', 'LUNCH', 'BREAK', 'LIBRARY', '-', 'UNKNOWN']:
                    continue
                parsed.append({
                    'subjectName': subj,
                    'code': cls.get('code') or cls.get('subjectCode') or cls.get('courseCode') or '',
                    'facultyName': cls.get('facultyName') or cls.get('faculty') or cls.get('employeeName') or '',
                    'time': time_to_24h(cls.get('time') or cls.get('startTime') or cls.get('classTime') or '00:00:00'),
                    'roomNo': cls.get('roomNo') or cls.get('room') or ''
                })
            if parsed:
                normalized[day] = parsed
        return normalized

    def scrape_full_data(self, username, password, target_pct=75.0):
        """Complete data pull with real-time credit and smart insights."""
        student_details = self.login(username, password)
        if not student_details:
            return None

        attendance = self.get_attendance(student_details)
        if not attendance:
            return None

        todays_classes = self.get_todays_classes(student_details)
        raw_tt = self.get_timetable(student_details)
        weekly_timetable = self._normalize_timetable(raw_tt)

        if not todays_classes and weekly_timetable:
            now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_name = now_ist.strftime('%A').capitalize()
            for c in weekly_timetable.get(today_name, []):
                todays_classes.append({
                    'subjectName': c['subjectName'],
                    'code': c['code'],
                    'facultyName': c['facultyName'],
                    'time': c['time'],
                    'roomNo': c['roomNo'],
                    'status': 'Not Updated',
                    'attPercentage': None
                })

        todays_present_counts = {}
        if todays_classes:
            for c in todays_classes:
                if c.get('status') == 'Present':
                    code_clean = str(c.get('code', '')).strip().upper()
                    name_clean = str(c.get('subjectName', '')).strip().upper()
                    if code_clean:
                        todays_present_counts[code_clean] = todays_present_counts.get(code_clean, 0) + 1
                    if name_clean and name_clean != code_clean:
                        todays_present_counts[name_clean] = todays_present_counts.get(name_clean, 0) + 1

        processed_attendance = []
        total_attended_official = 0
        total_conducted_official = 0
        total_attended_all = 0
        total_conducted_all = 0

        for s in attendance:
            try:
                attended = int(str(s.get('attended', 0)).strip() or 0)
                conducted = int(str(s.get('conducted', 0)).strip() or 0)
                raw_percentage = float(str(s.get('percentage', 0)).strip() or 0.0)
            except Exception:
                attended, conducted, raw_percentage = 0, 0, 0.0

            subj_code = str(s.get('code', '')).strip().upper()
            subj_name = str(s.get('subjectName', '')).strip().upper()

            present_today_count = max(
                todays_present_counts.get(subj_code, 0),
                todays_present_counts.get(subj_name, 0)
            )

            adjusted_attended = attended
            if present_today_count > 0 and conducted > 0:
                raw_ratio_pct = round((attended / conducted) * 100, 2)
                if abs(raw_percentage - raw_ratio_pct) < 0.8:
                    adjusted_attended = min(conducted, attended + present_today_count)

            insights = calculate_attendance_insights(adjusted_attended, conducted, target_pct)

            subj_record = {
                'subjectName': s.get('subjectName', 'Unknown'),
                'code': s.get('code', ''),
                'attended': adjusted_attended,
                'rawAttended': attended,
                'conducted': conducted,
                'percentage': insights['percentage'],
                'gemsPendingSync': (adjusted_attended > attended),
                'safeBunks': insights['safeBunks'],
                'classesNeeded': insights['classesNeeded'],
                'badgeColor': insights['badgeColor'],
                'message': insights['message']
            }
            processed_attendance.append(subj_record)

            is_non_core = subj_code in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']
            if not is_non_core:
                total_attended_official += adjusted_attended
                total_conducted_official += conducted

            total_attended_all += adjusted_attended
            total_conducted_all += conducted

        official_subs = [s for s in processed_attendance if s['code'].upper() not in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']]
        official_pct = round(sum(s['percentage'] for s in official_subs) / len(official_subs), 2) if official_subs else 0.0
        overall_all_pct = round(sum(s['percentage'] for s in processed_attendance) / len(processed_attendance), 2) if processed_attendance else 0.0

        official_insights = calculate_attendance_insights(total_attended_official, total_conducted_official, target_pct)
        all_insights = calculate_attendance_insights(total_attended_all, total_conducted_all, target_pct)

        return {
            'success': True,
            'isDemo': False,
            'lastUpdated': datetime.now(timezone.utc).isoformat(),
            'student': {
                'id': student_details.get('id', ''),
                'name': student_details.get('name', 'Student'),
                'usn': student_details.get('usn', username),
                'email': student_details.get('email', f"{username.lower()}@mitsims.in")
            },
            'overallAttendance': {
                'percentage': official_pct,
                'attended': total_attended_official,
                'conducted': total_conducted_official,
                'safeBunks': official_insights['safeBunks'],
                'classesNeeded': official_insights['classesNeeded'],
                'badgeColor': official_insights['badgeColor'],
                'label': 'Official Attendance (Academic Core)'
            },
            'overallWithTT': {
                'percentage': overall_all_pct,
                'attended': total_attended_all,
                'conducted': total_conducted_all,
                'safeBunks': all_insights['safeBunks'],
                'classesNeeded': all_insights['classesNeeded'],
                'label': 'Overall Attendance (All Courses)'
            },
            'attendance': processed_attendance,
            'todaysClasses': todays_classes,
            'weeklyTimetable': weekly_timetable,
            'targetPercentage': target_pct
        }


def get_demo_student_data(target_pct=75.0):
    """
    Returns realistic college student data for Web Campus instant demonstration.
    """
    raw_subjects = [
        {"subjectName": "Machine Learning & Deep Learning", "code": "20CST301", "attended": 28, "conducted": 32, "faculty": "Dr. Ramesh Babu"},
        {"subjectName": "Cloud Computing & DevOps", "code": "20CST302", "attended": 24, "conducted": 30, "faculty": "Prof. S. Swathi"},
        {"subjectName": "Database Management Systems", "code": "20CST303", "attended": 31, "conducted": 34, "faculty": "Dr. K. V. Sharma"},
        {"subjectName": "Compiler Design & Automata", "code": "20CST304", "attended": 21, "conducted": 30, "faculty": "Dr. P. Venkatesh"},
        {"subjectName": "Full Stack Web Development", "code": "20CST305", "attended": 26, "conducted": 28, "faculty": "Prof. M. Anitha"},
        {"subjectName": "Advanced Operating Systems Lab", "code": "20CSL301", "attended": 18, "conducted": 20, "faculty": "Dr. K. V. Sharma"},
        {"subjectName": "Technical Training & Coding", "code": "TECHNICALTRAINING", "attended": 25, "conducted": 30, "faculty": "Placement Cell"},
        {"subjectName": "Soft Skills & Aptitude", "code": "APTITUDE", "attended": 19, "conducted": 20, "faculty": "Prof. Harish Nair"}
    ]

    processed = []
    tot_att_off = 0
    tot_cond_off = 0
    tot_att_all = 0
    tot_cond_all = 0

    for s in raw_subjects:
        att = s['attended']
        cond = s['conducted']
        insights = calculate_attendance_insights(att, cond, target_pct)
        is_tt = s['code'] in ['TECHNICALTRAINING', 'APTITUDE']
        record = {
            'subjectName': s['subjectName'],
            'code': s['code'],
            'attended': att,
            'rawAttended': att,
            'conducted': cond,
            'percentage': insights['percentage'],
            'gemsPendingSync': False,
            'safeBunks': insights['safeBunks'],
            'classesNeeded': insights['classesNeeded'],
            'badgeColor': insights['badgeColor'],
            'message': insights['message'],
            'faculty': s['faculty']
        }
        processed.append(record)
        if not is_tt:
            tot_att_off += att
            tot_cond_off += cond
        tot_att_all += att
        tot_cond_all += cond

    off_subs = [s for s in processed if s['code'] not in ['TECHNICALTRAINING', 'APTITUDE']]
    official_pct = round(sum(s['percentage'] for s in off_subs) / len(off_subs), 2)
    overall_all_pct = round(sum(s['percentage'] for s in processed) / len(processed), 2)

    off_insights = calculate_attendance_insights(tot_att_off, tot_cond_off, target_pct)
    all_insights = calculate_attendance_insights(tot_att_all, tot_cond_all, target_pct)

    todays_classes = [
        {"subjectName": "Machine Learning & Deep Learning", "code": "20CSE301", "facultyName": "Dr. Ramesh Babu", "time": "09:00:00", "roomNo": "EB-302", "status": "Present", "attPercentage": "87.5%"},
        {"subjectName": "Cloud Computing & DevOps", "code": "20CSE302", "facultyName": "Prof. S. Swathi", "time": "10:00:00", "roomNo": "EB-302", "status": "Present", "attPercentage": "80.0%"},
        {"subjectName": "Compiler Design & Automata", "code": "20CSE304", "facultyName": "Dr. P. Venkatesh", "time": "11:15:00", "roomNo": "EB-302", "status": "Not Updated", "attPercentage": "70.0%"},
        {"subjectName": "Database Management Systems", "code": "20CSE303", "facultyName": "Dr. K. V. Sharma", "time": "13:30:00", "roomNo": "EB-302", "status": "Not Updated", "attPercentage": "91.2%"},
        {"subjectName": "Full Stack Web Development", "code": "20CSE305", "facultyName": "Prof. M. Anitha", "time": "14:30:00", "roomNo": "CS-LAB 4", "status": "Not Updated", "attPercentage": "92.8%"}
    ]

    weekly_timetable = {
        "Monday": [
            {"subjectName": "Machine Learning & Deep Learning", "code": "20CSE301", "facultyName": "Dr. Ramesh Babu", "time": "09:00:00", "roomNo": "EB-302"},
            {"subjectName": "Cloud Computing & DevOps", "code": "20CSE302", "facultyName": "Prof. S. Swathi", "time": "10:00:00", "roomNo": "EB-302"},
            {"subjectName": "Compiler Design & Automata", "code": "20CSE304", "facultyName": "Dr. P. Venkatesh", "time": "11:15:00", "roomNo": "EB-302"},
            {"subjectName": "Database Management Systems", "code": "20CSE303", "facultyName": "Dr. K. V. Sharma", "time": "13:30:00", "roomNo": "EB-302"},
            {"subjectName": "Full Stack Web Development", "code": "20CSE305", "facultyName": "Prof. M. Anitha", "time": "14:30:00", "roomNo": "CS-LAB 4"}
        ],
        "Tuesday": [
            {"subjectName": "Database Management Systems", "code": "20CSE303", "facultyName": "Dr. K. V. Sharma", "time": "09:00:00", "roomNo": "EB-302"},
            {"subjectName": "Compiler Design & Automata", "code": "20CSE304", "facultyName": "Dr. P. Venkatesh", "time": "10:00:00", "roomNo": "EB-302"},
            {"subjectName": "Technical Training & Coding", "code": "TECHNICALTRAINING", "facultyName": "Placement Cell", "time": "11:15:00", "roomNo": "AUD-1"},
            {"subjectName": "Advanced Operating Systems Lab", "code": "20CSL301", "facultyName": "Dr. K. V. Sharma", "time": "13:30:00", "roomNo": "CS-LAB 2"},
            {"subjectName": "Advanced Operating Systems Lab", "code": "20CSL301", "facultyName": "Dr. K. V. Sharma", "time": "14:30:00", "roomNo": "CS-LAB 2"}
        ],
        "Wednesday": [
            {"subjectName": "Cloud Computing & DevOps", "code": "20CSE302", "facultyName": "Prof. S. Swathi", "time": "09:00:00", "roomNo": "EB-302"},
            {"subjectName": "Machine Learning & Deep Learning", "code": "20CSE301", "facultyName": "Dr. Ramesh Babu", "time": "10:00:00", "roomNo": "EB-302"},
            {"subjectName": "Full Stack Web Development", "code": "20CSE305", "facultyName": "Prof. M. Anitha", "time": "11:15:00", "roomNo": "EB-302"},
            {"subjectName": "Soft Skills & Aptitude", "code": "APTITUDE", "facultyName": "Prof. Harish Nair", "time": "13:30:00", "roomNo": "EB-302"}
        ],
        "Thursday": [
            {"subjectName": "Compiler Design & Automata", "code": "20CSE304", "facultyName": "Dr. P. Venkatesh", "time": "09:00:00", "roomNo": "EB-302"},
            {"subjectName": "Database Management Systems", "code": "20CSE303", "facultyName": "Dr. K. V. Sharma", "time": "10:00:00", "roomNo": "EB-302"},
            {"subjectName": "Machine Learning & Deep Learning", "code": "20CSE301", "facultyName": "Dr. Ramesh Babu", "time": "11:15:00", "roomNo": "EB-302"},
            {"subjectName": "Cloud Computing & DevOps", "code": "20CSE302", "facultyName": "Prof. S. Swathi", "time": "13:30:00", "roomNo": "EB-302"}
        ],
        "Friday": [
            {"subjectName": "Full Stack Web Development", "code": "20CSE305", "facultyName": "Prof. M. Anitha", "time": "09:00:00", "roomNo": "EB-302"},
            {"subjectName": "Technical Training & Coding", "code": "TECHNICALTRAINING", "facultyName": "Placement Cell", "time": "10:00:00", "roomNo": "AUD-1"},
            {"subjectName": "Compiler Design & Automata", "code": "20CSE304", "facultyName": "Dr. P. Venkatesh", "time": "11:15:00", "roomNo": "EB-302"},
            {"subjectName": "Soft Skills & Aptitude", "code": "APTITUDE", "facultyName": "Prof. Harish Nair", "time": "13:30:00", "roomNo": "EB-302"}
        ],
        "Saturday": [
            {"subjectName": "Machine Learning & Deep Learning", "code": "20CSE301", "facultyName": "Dr. Ramesh Babu", "time": "09:30:00", "roomNo": "EB-302"},
            {"subjectName": "Cloud Computing & DevOps", "code": "20CSE302", "facultyName": "Prof. S. Swathi", "time": "10:30:00", "roomNo": "EB-302"}
        ]
    }

    return {
        'success': True,
        'isDemo': True,
        'lastUpdated': datetime.now(timezone.utc).isoformat(),
        'student': {
            'id': '98432',
            'name': 'Sai Kumar Reddy',
            'usn': '21691A28B4',
            'email': '21691a28b4@mits.ac.in',
            'college': 'Madanapalle Institute of Technology & Science'
        },
        'overallAttendance': {
            'percentage': official_pct,
            'attended': tot_att_off,
            'conducted': tot_cond_off,
            'safeBunks': off_insights['safeBunks'],
            'classesNeeded': off_insights['classesNeeded'],
            'badgeColor': off_insights['badgeColor'],
            'label': 'Official Attendance (Core Academic)'
        },
        'overallWithTT': {
            'percentage': overall_all_pct,
            'attended': tot_att_all,
            'conducted': tot_cond_all,
            'safeBunks': all_insights['safeBunks'],
            'classesNeeded': all_insights['classesNeeded'],
            'label': 'Overall Attendance (All Subjects & Labs)'
        },
        'attendance': processed,
        'todaysClasses': todays_classes,
        'weeklyTimetable': weekly_timetable,
        'targetPercentage': target_pct
    }
