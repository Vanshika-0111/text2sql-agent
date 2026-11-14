def load_schema_parts(path="table_descriptions.txt"):
    """Split table_descriptions.txt into the top block and one chunk per table."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    pieces = text.split("\nTABLE: ")
    header = pieces[0].strip()          # database info, joins, common paths, general rules

    tables = {}
    for piece in pieces[1:]:
        name = piece.split("\n", 1)[0].strip()      # first line is the table name
        tables[name] = "TABLE: " + piece.strip()
    return header, tables


def build_schema(header, tables, names):
    """Join the top block with only the chosen tables."""
    chunks = [tables[n] for n in names]
    return header + "\n\n" + "\n\n".join(chunks)


if __name__ == "__main__":
    header, tables = load_schema_parts()
    print(f"Header: {len(header)} characters")
    for name, chunk in tables.items():
        print(f"  {name:<22} {len(chunk):>5} characters")

    full = build_schema(header, tables, list(tables))
    small = build_schema(header, tables, ["orders"])
    print(f"\nFull schema:    {len(full)} characters")
    print(f"Only 'orders':  {len(small)} characters")