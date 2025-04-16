import pdfplumber
import warnings
from collections import defaultdict

# # Ignorar o aviso relacionado ao CropBox
# warnings.filterwarnings("ignore", message="CropBox missing from /Page, defaulting to MediaBox")
#
# # Dicionário para agrupar textos por doctop
# doctop_texts = defaultdict(list)
#
# with pdfplumber.open("/home/neimog/Downloads/EduardoFabricioLuciukFrigatti.pdf") as pdf:
#     page = pdf.pages[21]  # Página de índice 20 (21ª página)
#     words = page.extract_words()
#     for word in words:
#         print(word)



from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def cosine_similarity_score(text1, text2):
    # Usar TF-IDF para representar os textos
    tfidf_vectorizer = TfidfVectorizer()
    tfidf_matrix = tfidf_vectorizer.fit_transform([text1, text2])
    
    # Calcular a similaridade de cosseno
    similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])
    return similarity[0][0]

text1 = "40 August 2006/Vol. 49, No. 8 COMMUNICATIONS OF THE ACM"
text2 = "COMMUNICATIONS OF THE ACM August 2006/Vol. 49, No. 8 41"
similarity = cosine_similarity_score(text1, text2)
print(f"Similaridade de Cosseno: {similarity}")

