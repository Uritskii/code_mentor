import json
import subprocess
import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

# Каталог задач и файл профиля студента лежат рядом со скриптом
BASE_DIR = Path(__file__).parent
TASKS = {t["id"]: t for t in json.loads((BASE_DIR / "tasks.json").read_text(encoding="utf-8"))}
PROFILE_PATH = BASE_DIR / "student_profile.json"


# Реестр кода, который действительно присылал студент (заполняет harness).
# run_tests разрешено запускать только код из этого реестра.
_SUBMITTED_CODE: set[str] = set()


def register_submitted_code(code: str) -> None:
    """Вызывает harness: фиксирует код из сообщения студента."""
    _SUBMITTED_CODE.add(code.strip())


class ToolError(Exception):
    """Ожидаемая ошибка инструмента — уйдёт модели как данные, а не как traceback."""


# ---------------------------------------------------------------- схемы аргументов

class GetTaskArgs(BaseModel):
    task_id: Literal["sum_two", "count_even", "max_of_three"] = Field(
        description="Идентификатор задачи из каталога"
    )


class RunTestsArgs(BaseModel):
    task_id: Literal["sum_two", "count_even", "max_of_three"] = Field(
        description="Идентификатор задачи, к которой относится код"
    )
    code: str = Field(
        description="Полный текст решения студента на Python. "
                    "Программа читает входные данные из stdin и печатает ответ.",
        max_length=10_000,
    )


class RecordMistakeArgs(BaseModel):
    task_id: str = Field(description="Задача, в которой ошибся студент")
    category: Literal[
        "input_parsing",   # неверно прочитал входные данные
        "boundary",        # граничные случаи: ноль, пустой ввод, отрицательные
        "off_by_one",      # ошибка на единицу в циклах/индексах
        "logic",           # неверная логика решения
        "output_format",   # формат вывода не совпадает с требуемым
    ] = Field(description="Категория ошибки — строго из списка")
    note: str = Field(description="Одно предложение: в чём именно ошибка", max_length=300)


# ---------------------------------------------------------------- реализация

def get_task(args: GetTaskArgs) -> dict:
    """Возвращает условие задачи. Тесты намеренно не отдаются —
    модель не должна видеть эталонные ответы."""
    task = TASKS.get(args.task_id)
    if task is None:
        raise ToolError(f"Задача '{args.task_id}' не найдена. Есть: {list(TASKS)}")
    return {"task_id": task["id"], "title": task["title"], "statement": task["statement"]}


def run_tests(args: RunTestsArgs) -> dict:
    """Запускает код студента на тестах задачи в отдельном процессе с таймаутом.

    Возвращает агрегированный результат: сколько тестов пройдено, тип первой
    ошибки и вывод студента на упавшем тесте. Эталонный ответ НЕ возвращается —
    это защита от утечки решения через инструмент.
    """
    # Страж в коде: модель может проверять только код, присланный студентом.
    # Свои собственные «исправленные» варианты запускать запрещено —
    # иначе агент фабрикует успех вместо честной проверки.
    if args.code.strip() not in _SUBMITTED_CODE:
        raise ToolError(
            "Этот код не присылал студент. Проверять можно только код из сообщений "
            "студента. Если хочешь показать идею исправления — опиши её словами."
        )

    task = TASKS.get(args.task_id)
    if task is None:
        raise ToolError(f"Задача '{args.task_id}' не найдена")

    passed = 0
    first_failure = None
    for i, test in enumerate(task["tests"], start=1):
        try:
            proc = subprocess.run(
                [sys.executable, "-c", args.code],
                input=test["input"],
                capture_output=True,
                text=True,
                timeout=3,  # клиент не будет ждать вечно
            )
        except subprocess.TimeoutExpired:
            first_failure = {"test": i, "type": "timeout",
                             "message": "Программа не завершилась за 3 секунды (возможен бесконечный цикл)"}
            break

        actual = proc.stdout.strip()
        if proc.returncode != 0:
            # stderr режем до первой строки — не тащим traceback в контекст
            err_line = (proc.stderr.strip().splitlines() or ["unknown"])[-1]
            first_failure = {"test": i, "type": "runtime_error", "message": err_line}
            break
        if actual == test["expected"].strip():
            passed += 1
        else:
            # ВАЖНО: эталонный ответ сюда не кладём
            first_failure = {"test": i, "type": "wrong_answer",
                             "input": test["input"], "student_output": actual}
            break

    total = len(task["tests"])
    return {
        "passed": passed,
        "total": total,
        "all_passed": passed == total,
        "first_failure": first_failure,
    }


def record_mistake(args: RecordMistakeArgs) -> dict:
    """Действие: пишет ошибку в профиль студента на диске.

    Идемпотентность: та же категория в той же задаче не дублируется,
    а увеличивает счётчик — повторный вызов безопасен.
    """
    if args.task_id not in TASKS:
        raise ToolError(f"Задача '{args.task_id}' не найдена")

    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {"mistakes": []}
    for m in profile["mistakes"]:
        if m["task_id"] == args.task_id and m["category"] == args.category:
            m["count"] += 1
            m["note"] = args.note
            break
    else:
        profile["mistakes"].append(
            {"task_id": args.task_id, "category": args.category, "note": args.note, "count": 1}
        )
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": True, "total_mistakes_in_profile": len(profile["mistakes"])}


# ---------------------------------------------------------------- реестр и harness

TOOLS = {
    "get_task": {
        "description": "Возвращает условие учебной задачи по её id. Вызывай, когда нужно сверить решение студента с условием.",
        "args_model": GetTaskArgs,
        "fn": get_task,
    },
    "run_tests": {
        "description": "Запускает код студента на скрытых тестах задачи и возвращает, сколько тестов пройдено и где первая ошибка. "
                       "Эталонные ответы не раскрываются. Вызывай всегда, когда студент присылает код.",
        "args_model": RunTestsArgs,
        "fn": run_tests,
    },
    "record_mistake": {
        "description": "Записывает ошибку студента в его долговременный профиль. "
                       "Вызывай один раз, когда ошибка точно установлена и разобрана.",
        "args_model": RecordMistakeArgs,
        "fn": record_mistake,
    },
}


def tool_schemas() -> list[dict]:
    """Схемы в формате OpenAI tools — это то, что видит модель."""
    schemas = []
    for name, tool in TOOLS.items():
        schema = tool["args_model"].model_json_schema()
        # pydantic кладёт описание полей внутрь properties — формат совместим
        schemas.append({
            "type": "function",
            "function": {"name": name, "description": tool["description"], "parameters": schema},
        })
    return schemas


def execute_tool(name: str, raw_args: dict) -> dict:
    """Harness: проверить аргументы -> выполнить -> ошибку вернуть как данные.

    Модель участвует только в выборе вызова; всё остальное делает этот код.
    """
    print(f"[вызов инструмента] {name}({raw_args})")  # трассировка вызовов
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": {"type": "unknown_tool", "message": f"Инструмента '{name}' нет",
                          "hint": f"Доступны: {list(TOOLS)}"}}
    try:
        args = tool["args_model"].model_validate(raw_args)
    except ValidationError as e:
        # Сообщение об ошибке пишем для модели: что не так и что допустимо
        first = e.errors()[0]
        return {"error": {"type": "validation", "field": ".".join(map(str, first["loc"])),
                          "message": first["msg"], "hint": "Исправь аргументы и повтори вызов"}}
    try:
        return {"result": tool["fn"](args)}
    except ToolError as e:
        return {"error": {"type": "tool_error", "message": str(e)}}
    except Exception as e:  # неожиданное — тоже как данные, агент не должен падать
        return {"error": {"type": "internal", "message": f"{type(e).__name__}: {e}",
                          "hint": "Сообщи студенту, что проверка сейчас недоступна"}}
