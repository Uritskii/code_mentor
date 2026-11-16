import json
import re

from providers import get_client_and_model
from tools import execute_tool, tool_schemas, register_submitted_code


SYSTEM_PROMPT = """Ты — наставник по программированию для начинающего студента.

Правила:
1. Никогда не пиши за студента готовое решение и не показывай исправленный код целиком.
2. Веди к решению ступенчатыми подсказками:
   - Уровень 1 (первая ошибка): назови область и характер ошибки СВОИМИ СЛОВАМИ. ЗАПРЕЩЕНО приводить правильное условие, выражение, оператор или фрагмент исправленного кода.
   - Уровень 2 (студент не справился): опиши идею исправления словами, всё ещё без кода.
   - Уровень 3 (несколько неудачных попыток): можно показать фрагмент с исправлением.
   Пример уровня 1. ПЛОХО: «замени x % 2 == 1 на x % 2 == 0». ХОРОШО: «твой фильтр отбирает не ту группу чисел — перечитай условие задачи и подумай, остаток от деления на 2 у каких чисел нужно считать».
3. Когда студент присылает код, всегда проверяй его инструментом run_tests, не гадай. Проверять можно ТОЛЬКО код, который прислал студент; свой вариант решения запускать запрещено.
4. Опирайся на условие задачи — при необходимости возьми его через get_task.
5. Когда ошибка точно установлена и разобрана, запиши её через record_mistake.
6. Тесты скрыты: эталонных ответов у тебя нет, не выдумывай их.
7. Отвечай кратко, по-русски, дружелюбно."""


def _confirm_action(name: str, args) -> bool:
    """Подтверждение действия человеком.
    Harness показывает студенту конкретные параметры действия и выполняет
    ровно их — и только после «да». Фраза модели «студент согласен»
    подтверждением не считается.
    """
    print(f"\n[требуется подтверждение] Наставник хочет выполнить действие:")
    print(f"  инструмент: {name}")
    print(f"  параметры:  {args.model_dump()}")
    answer = input("Подтвердить? [да/нет] ")
    return answer.strip().lower() in {"да", "д", "yes", "y"}


def run_agent(user_message: str, history: list | None = None, max_steps: int = 6) -> str:
    """Один диалоговый ход: цикл до финального ответа или лимита шагов."""
    # Harness извлекает блоки кода из сообщения студента и фиксирует их:
    for block in re.findall(r"```(?:python)?\s*\n(.*?)```", user_message, re.DOTALL):
        register_submitted_code(block)

    client, model = get_client_and_model()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})

    for step in range(1, max_steps + 1):
        resp = client.chat.completions.create(
            model=model, messages=messages,
            tools=tool_schemas(), tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        # выход 1: инструменты не нужны — это финальный ответ студенту
        if not msg.tool_calls:
            # Страж в коде: студент прислал код, а модель не проверила его
            # тестами — ответ не отдаём, возвращаем модель в цикл с напоминанием.
            code_sent = "```" in user_message
            tests_ran = any(m.get("role") == "tool" for m in messages)
            if code_sent and not tests_ran:
                messages.append({"role": "user", "content":
                    "[напоминание harness] Студент прислал код. Сначала проверь его "
                    "через run_tests, потом отвечай."})
                continue
            return msg.content

        # действие и наблюдение: выполнить, результат -> в контекст
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                args = {}  # пусть валидация вернёт модели понятную ошибку
            result = execute_tool(call.function.name, args, confirm=_confirm_action)
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    # выход 2: лимит шагов исчерпан — решение принял harness, не модель
    return ("Я запутался в шагах и остановился, чтобы не тратить твоё время. "
            "Давай начнём этот вопрос заново, короче.")
