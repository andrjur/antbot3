import os


def is_excluded_directory(path, exclude_dirs=None):
    """Проверяет, является ли путь исключенным каталогом."""
    if exclude_dirs is None:
        exclude_dirs = {".git", "__pycache__", ".idea", ".venv"}

    parts = path.split(os.sep)
    return any(part.startswith(".") or part in exclude_dirs for part in parts)


def extract_file_metadata(file_path):
    """Извлекает метаданные из Python-файла (импорты, функции, docstrings)."""
    imports, functions = [], []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    imports.append(stripped)
                elif stripped.startswith("def "):
                    functions.append(stripped.split("def ")[1].split("(")[0])
    except Exception:
        return 0, 0

    return len(imports), len(functions)


def get_codebase_summary(path, exclude_dirs=None):
    """Генерирует сводку по кодовой базе с группировкой по файлам."""
    if exclude_dirs is None:
        exclude_dirs = {".git", "__pycache__", ".idea", ".venv"}

    output = []
    for root, _, files in os.walk(path):
        if is_excluded_directory(root, exclude_dirs):
            continue

        for file in files:
            if not file.endswith(".py"):
                continue

            full_path = os.path.join(root, file)
            import_count, func_count = extract_file_metadata(full_path)

            file_header = f"📄 {full_path} [импортов: {import_count}, функций: {func_count}]"
            output.append(file_header)

            with open(full_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(("import ", "from ")):
                        output.append("    " + stripped)
                    if stripped.startswith(("def ", "async def ")):
                        output.append("\n    " + stripped)

            output.append("\n" * 2)

    return "\n".join(output)


def main():
    """Точка входа в программу."""
    current_dir = os.getcwd()
    output_file = "codebase_summary.txt"

    if os.path.exists(output_file):
        os.remove(output_file)

    summary = get_codebase_summary(current_dir)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(summary)

    print(f"Сводка по кодовой базе сохранена в {output_file}")


if __name__ == "__main__":
    main()
