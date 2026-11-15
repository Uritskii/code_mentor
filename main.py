from agent import run_agent


def main():
    print("Code Mentor. Задачи: sum_two, count_even, max_of_three. Выход: /exit")
    history = []  # «память» агента — это просто список сообщений, который
                  # harness заново передаёт модели на каждом шаге
    while True:
        user = input("\nВы: ").strip()
        if user.lower() in {"/exit", "exit", "quit"}:
            break
        answer = run_agent(user, history)
        # сохраняем ход в историю диалога
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": answer})
        print(f"\nНаставник: {answer}")


if __name__ == "__main__":
    main()
