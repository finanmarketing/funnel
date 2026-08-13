# Журнал изменений БД dwh_ezru_loans

## 2026-08-02 — Ф1.1 (E. Rybakov)
**Что сделано:** создана схема `metrica_raw` + таблицы `goals_dict`, `pipeline_runs` + 2 индекса.
**Схема public:** не затронута, доступ только на чтение (`public.clients`).
**Откат:** `pipeline/sql/99_rollback_metrica_raw.sql` — сносит схему целиком, побочных эффектов нет.
**Проверено до накатки:** схемы `metrica_raw` в базе не существовало.
**Контроль после:** 2 таблицы, INSERT/DELETE smoke-тест пройден.

# Журнал изменений БД dwh_ezru_loans

## 2026-08-02 — Ф1.1 (E. Rybakov, учётка risk_erybakov)
**Права:** CREATE SCHEMA недоступен (DB CREATE=False). Работаем внутри существующей схемы `dwh_ezru_loans` по роли `dwh_ezru_loans_rw`.
**Создано:** `dwh_ezru_loans.metrica_goals_dict`, `dwh_ezru_loans.metrica_pipeline_runs` + индексы `ix_metrica_runs_started`, `ix_metrica_runs_stage_status`.
**Схема public:** не затронута. Доступ к `public.clients` только на чтение.
**Изоляция:** все объекты пайплайна имеют префикс `metrica_`. Соседние таблицы схемы не трогаются.
**Откат:** `pipeline/sql/99_rollback_metrica_raw.sql` — DROP трёх таблиц поимённо. DROP SCHEMA запрещён: схема чужая.
**Проверено до накатки:** таблиц с префиксом `metrica%` в схеме не было (0 строк).
## 2026-08-13 ������ �� ������� �����
������ �����: ������� -> ������� (UserID �� parsedParams = clients.client_number).
�������� ������� metrica_person_map. ����� ����������: ��� 39158->39119, ���� 30034->29995.
������ ������ ��������� � ��������� � ����� �� �����������.
������ check_history ������������� ������� ����������: ����� -0.5% �� 60 ����.
