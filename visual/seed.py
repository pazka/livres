"""Fixed data for the visual harness (hosh-management AGENTS.md section 3), run inside the container
(`docker exec … python /dev/stdin < visual/seed.py`) before any shot, with the scheduler off.

- The only live-search source (ebooksgratuits) is switched off by an override, so a search never leaves
  the container: results come from the harvested index only, filled here with fixed books.
- 22 books match "Victor Hugo" (20 per page: page 1 shows "Page suivante"), 2 more for other queries.
- One book request already in the admin list.
Nothing here is a real person's data. Running it twice gives the same database content.
"""
from livres.config import Settings
from livres.domain import Acquisition, Book
from livres.store import Store

s = Settings.from_env()
store = Store(s.db_path)
store.set_override("ebooksgratuits", False, "visual harness: no network", "qa")

EPUB, MOBI = "application/epub+zip", "application/x-mobipocket-ebook"
HUGO = [
    "Les Misérables", "Notre-Dame de Paris", "Les Contemplations", "Les Châtiments", "Hernani",
    "Ruy Blas", "Quatrevingt-treize", "L'Homme qui rit", "Les Travailleurs de la mer",
    "Le Dernier Jour d'un condamné", "Claude Gueux", "Les Orientales", "Les Feuilles d'automne",
    "Les Chants du crépuscule", "Les Voix intérieures", "Les Rayons et les Ombres",
    "La Légende des siècles", "Lucrèce Borgia", "Marie Tudor", "Cromwell", "Bug-Jargal",
    "Han d'Islande",
]
books = [
    Book(identifier=f"gutenberg:{1000 + i}", source_id="gutenberg", title=t, authors=("Victor Hugo",),
         acquisitions=(Acquisition(f"https://www.gutenberg.org/ebooks/{1000 + i}.epub3.images", EPUB),
                       Acquisition(f"https://www.gutenberg.org/ebooks/{1000 + i}.kf8.images", MOBI)))
    for i, t in enumerate(HUGO)
]
books.append(Book(identifier="gutenberg:2001", source_id="gutenberg", title="Le Tour du monde en quatre-vingts jours",
                  authors=("Jules Verne",),
                  acquisitions=(Acquisition("https://www.gutenberg.org/ebooks/2001.epub3.images", EPUB),)))
store.replace_source_books("gutenberg", books)
store.replace_source_books("beq", [
    Book(identifier="beq:madame-bovary", source_id="beq", title="Madame Bovary", authors=("Gustave Flaubert",),
         acquisitions=(Acquisition("https://beq.ebooksgratuits.com/vents/Flaubert-Bovary.epub", EPUB),)),
])
if not store.list_requests():
    store.add_request("Le Comte de Monte-Cristo", "Alexandre Dumas", "Pour ma lecture du soir, merci.", "", "seed")
print("seeded")
