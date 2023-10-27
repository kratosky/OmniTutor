import pandas as pd
import numpy as np
import faiss
import openai
import tempfile
from sentence_transformers import SentenceTransformer
import streamlit as st
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from collections import Counter
import nltk
import time

openai.api_key = st.secrets["OPENAI_API_KEY"]

@st.cache_data
def download_nltk():
    nltk.download('punkt')
    nltk.download('wordnet')
    nltk.download('stopwords')

def chunkstring(string, length):
        return (string[0+i:length+i] for i in range(0, len(string), length))

def get_keywords(file_paths): #这里的重点是，对每一个file做尽可能简短且覆盖全面的summarization
    download_nltk()
    keywords_list = []
    for file_path in file_paths:
        with open(file_path, 'r') as file:
            data = file.read()
            # tokenize
            words = word_tokenize(data)
            # remove punctuation
            words = [word for word in words if word.isalnum()]
            # remove stopwords
            stop_words = set(stopwords.words('english'))
            words = [word for word in words if word not in stop_words]
            # lemmatization
            lemmatizer = WordNetLemmatizer()
            words = [lemmatizer.lemmatize(word) for word in words]
            # count word frequencies
            word_freq = Counter(words)
            # get top 20 most common words
            keywords = word_freq.most_common(20)
            new_keywords = []
            for word in keywords:
                new_keywords.append(word[0])
            str_keywords = ''
            for word in new_keywords:
                str_keywords += word + ", "
            keywords_list.append(f"Top20 frequency keywords for {file_path}: {str_keywords}")

    return keywords_list

def get_completion_from_messages(messages, model="gpt-4", temperature=0):
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=temperature, # this is the degree of randomness of the model's output
        )
        return response.choices[0].message["content"]

#调用gpt API生成课程大纲 + 每节课解释，随后输出为md文档。并在课程内一直保留着
def genarating_outline(keywords, num_lessons,language):

    system_message = 'You are a great AI teacher and linguist, skilled at create course outline based on summarized knowledge materials.'
    user_message = f"""You are a great AI teacher and linguist,
            skilled at generating course outline based on keywords of the course.
            Based on keywords provided, you should carefully design a course outline. 
            Requirements: Through learning this course, learner should understand those key concepts.
            Key concepts: {keywords}
            you should output course outline in a python list format, Do not include anything else except that python list in your output.
            Example output format:
            [[name_lesson1, abstract_lesson1],[name_lesson2, abstrct_lesson2]]
            In the example, you can see each element in this list consists of two parts: the "name_lesson" part is the name of the lesson, and the "abstract_lesson" part is the one-sentence description of the lesson, intruduces knowledge it contained. 
            for each lesson in this course, you should provide these two information and organize them as exemplified.
            for this course, you should design {num_lessons} lessons in total.
            the course outline should be written in {language}.
            Start the work now.
            """
    messages =  [
                {'role':'system',
                'content': system_message},
                {'role':'user',
                'content': user_message},
            ]

    response = get_completion_from_messages(messages)

    list_response = ['nothing in the answers..']

    try:
        list_response = eval(response)
    except SyntaxError:
        pass

    return list_response

def courseOutlineGenerating(file_paths, num_lessons, language):
    summarized_materials = get_keywords(file_paths)
    course_outline = genarating_outline(summarized_materials, num_lessons, language)
    return course_outline

def constructVDB(file_paths):
#把KM拆解为chunks

    chunks = []
    for filename in file_paths:
        with open(filename, 'r') as f:
            content = f.read()
            for chunk in chunkstring(content, 730):
                chunks.append(chunk)
    chunk_df = pd.DataFrame(chunks, columns=['chunk'])

    #从文本chunks到embeddings
    model = SentenceTransformer('paraphrase-mpnet-base-v2')
    embeddings = model.encode(chunk_df['chunk'].tolist())
    # convert embeddings to a dataframe
    embedding_df = pd.DataFrame(embeddings.tolist())
    # Concatenate the original dataframe with the embeddings
    paraphrase_embeddings_df = pd.concat([chunk_df, embedding_df], axis=1)
    # Save the results to a new csv file

    #从embeddings到向量数据库
    # Load the embeddings
    data = paraphrase_embeddings_df
    embeddings = data.iloc[:, 1:].values  # All columns except the first (chunk text)

    # Ensure that the array is C-contiguous
    embeddings = np.ascontiguousarray(embeddings, dtype=np.float32)
    # Preparation for Faiss
    dimension = embeddings.shape[1]  # the dimension of the vector space
    index = faiss.IndexFlatL2(dimension)
    # Normalize the vectors
    faiss.normalize_L2(embeddings)
    # Build the index
    index.add(embeddings)
    # write index to disk
    return paraphrase_embeddings_df, index

def searchVDB(search_sentence, paraphrase_embeddings_df, index):
    #从向量数据库中检索相应文段
    try:
        data = paraphrase_embeddings_df
        embeddings = data.iloc[:, 1:].values  # All columns except the first (chunk text)
        embeddings = np.ascontiguousarray(embeddings, dtype=np.float32)

        model = SentenceTransformer('paraphrase-mpnet-base-v2')
        sentence_embedding = model.encode([search_sentence])

        # Ensuring the sentence embedding is in the correct format
        sentence_embedding = np.ascontiguousarray(sentence_embedding, dtype=np.float32)
        # Searching for the top 3 nearest neighbors in the FAISS index
        D, I = index.search(sentence_embedding, k=3)
        # Printing the top 3 most similar text chunks
        retrieved_chunks_list = []
        for idx in I[0]:
            retrieved_chunks_list.append(data.iloc[idx].chunk)

    except Exception:
        retrieved_chunks_list = []
        
    return retrieved_chunks_list

def generateCourse(topic, materials, language):

    #调用gpt4 API生成一节课的内容
    system_message = 'You are a great AI teacher and linguist, skilled at writing informative and easy-to-understand course script based on given lesson topic and knowledge materials.'

    user_message = f"""You are a great AI teacher and linguist,
            skilled at writing informative and easy-to-understand course script based on given lesson topic and knowledge materials.
            You should write a course for new hands, they need detailed and vivid explaination to understand the topic. 
            A high-quality course should meet requirements below:
            (1) Contains enough facts, data and figures to be convincing
            (2) The internal narrative is layered and logical, not a simple pile of items
            Make sure all these requirements are considered when writing the lesson script content.
            Please follow this procedure step-by-step when disgning the course:
            Step 1. Write down the teaching purpose of the lesson initially in the script.
            Step 2. Write down the outline of this lesson (outline is aligned to the teaching purpose), then follow the outline to write the content. Make sure every concept in the outline is explined adequately in the course.
            Your lesson topic and abstract is within the 「」 quotes, and the knowledge materials are within the 【】 brackets.
            lesson topic and abstract: 「{topic}」,
            knowledge materials related to this lesson：【{materials} 】
            the script should be witten in {language}.
            Start writting the script of this lesson now.
            """

    messages =  [
                {'role':'system',
                'content': system_message},
                {'role':'user',
                'content': user_message},
            ]

    response = get_completion_from_messages(messages)
    return response

def decorate_user_question(user_question, retrieved_chunks_for_user):
    decorated_prompt = f'''You're a brilliant teaching assistant, skilled at answer stundent's question based on given materials.
    student's question: 「{user_question}」
    related materials:【{retrieved_chunks_for_user}】
    if the given materials are irrelavant to student's question, please use your own knowledge to answer the question.
    You need to break down the student's question first, find out what he really wants to ask, and then try your best to give a comprehensive answer.
    The language you're answering in should aligned with what student is using.
    Now you're talking to the student. Please answer.
    '''
    return decorated_prompt

def app():
    st.title("OmniTutor v0.0.2")

    if "openai_model" not in st.session_state:
        st.session_state["openai_model"] = "gpt-3.5-turbo"
        # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []
    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    with st.sidebar:
        st.image("https://siyuan-harry.oss-cn-beijing.aliyuncs.com/oss://siyuan-harry/20231021212525.png")
        added_files = st.file_uploader('Upload .md file', type=['.md'], accept_multiple_files=True)
        num_lessons = st.slider('How many lessons do you want this course to have?', min_value=3, max_value=15, value=5, step=1)
        language = 'English'
        Chinese = st.checkbox('Output in Chinese')
        if Chinese:
            language = 'Chinese'
        btn = st.button('submit')
    
    col1, col2 = st.columns([0.6,0.4])

    user_question = st.chat_input("Enter your questions when learning... (after submit your materials)")

    with col2:
        st.caption(''':blue[AI Assistant]: Ask this TA any questions related to this course and get direct answers. :sunglasses:''')
            # Set a default model

        with st.chat_message("assistant"):
            st.write("Hello👋, how can I help you today? 😄")
        
        #这里的session.state就是保存了这个对话会话的一些基本信息和设置
        if user_question:
            retrieved_chunks_for_user = searchVDB(user_question, embeddings_df, faiss_index)
            #retrieved_chunks_for_user = []
            prompt = decorate_user_question(user_question, retrieved_chunks_for_user)
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(user_question)
            # Display assistant response in chat message container
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""
                for response in openai.ChatCompletion.create(
                    model=st.session_state["openai_model"],
                    messages=[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages],
                    stream=True,
                ):
                    full_response += response.choices[0].delta.get("content", "")
                    message_placeholder.markdown(full_response + "▌")
                message_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
        
    if btn:
        temp_file_paths = []
        file_proc_state = st.empty()
        file_proc_state.text("Processing file...")
        for added_file in added_files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".md") as tmp:
                tmp.write(added_file.getvalue())
                tmp_path = tmp.name
                temp_file_paths.append(tmp_path)
        file_proc_state.text("Processing file...Done")

        vdb_state = st.empty()
        vdb_state.text("Constructing vector database from provided materials...")
        embeddings_df, faiss_index = constructVDB(temp_file_paths)
        vdb_state.text("Constructing vector database from provided materials...Done")
        
        outline_generating_state = st.empty()
        outline_generating_state.text("Generating Course Outline...")
        course_outline_list = courseOutlineGenerating(temp_file_paths, num_lessons, language)
        outline_generating_state.text("Generating Course Outline...Done")

        file_proc_state.empty()
        vdb_state.empty()
        outline_generating_state.empty()
        
        with col1:
            st.text("Processing file...Done")
            st.text("Constructing vector database from provided materials...Done")
            st.text("Generating Course Outline...Done")

            #把课程大纲打印出来
            course_outline_string = ''
            lessons_count = 0
            for outline in course_outline_list:
                lessons_count += 1
                course_outline_string += f"{lessons_count}." + outline[0]
                course_outline_string += '\n' + outline[1] + '\n\n'
                #time.sleep(1)
            with st.expander("Check the course outline", expanded=False):
                        st.write(course_outline_string)

            count_generating_content = 0
            for lesson in course_outline_list:
                count_generating_content += 1
                content_generating_state = st.text(f"Writing content for lesson {count_generating_content}...")
                retrievedChunksList = searchVDB(lesson, embeddings_df, faiss_index)
                courseContent = generateCourse(lesson, retrievedChunksList, language)
                content_generating_state.text(f"Writing content for lesson {count_generating_content}...Done")
                #st.text_area("Course Content", value=courseContent)
                with st.expander(f"Learn the lesson {count_generating_content} ", expanded=False):
                    st.markdown(courseContent)
    
if __name__ == "__main__":
    app()