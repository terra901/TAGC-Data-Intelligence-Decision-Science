# 将你的数据直接粘贴到下面的三个引号之间
raw_data = """


SQL ID	得分
sql_1	0
sql_2	0
sql_3	0
sql_4	0
sql_6	0
sql_7	0
sql_8	0
sql_9	0
sql_10	0
sql_11	0
sql_13	0
sql_14	0
sql_15	0
sql_16	0
sql_18	0
sql_19	0
sql_20	0
sql_22	0
sql_23	0
sql_24	0
sql_26	0
sql_27	0
sql_29	0
sql_31	0
sql_32	0
sql_40	0
sql_41	0
sql_42	0
sql_43	0
sql_44	0
sql_47	0
sql_50	0
sql_51	0
sql_52	0
sql_53	0
sql_54	0
sql_55	0
sql_58	0
sql_60	0
sql_61	0
sql_62	0
sql_64	0
sql_65	0
sql_67	0
sql_68	0
sql_69	0
sql_70	0
sql_71	0
sql_73	0
sql_74	0
sql_75	0
sql_77	0
sql_78	0
sql_80	0
sql_82	0
sql_83	0
sql_84	0
sql_85	0
sql_87	0
sql_88	0
sql_89	0
sql_90	0
sql_91	0
sql_92	1
sql_93	0
sql_94	1
sql_97	1
sql_98	0
sql_99	0
sql_100	0
sql_101	1
sql_103	1
sql_105	1
sql_107	1
sql_108	0
sql_109	0
sql_110	0
sql_111	0
sql_112	1
sql_113	1
sql_115	0
sql_116	1
sql_117	0
sql_118	1
sql_119	1
sql_120	0
"""

def parse_data(data):
    correct_list = []
    incorrect_list = []

    # 按行分割，并去除首尾空白
    lines = data.strip().split('\n')

    for line in lines:
        # 去除行首尾空白
        line = line.strip()
        if not line: continue # 跳过空行

        # 自动处理 tab 或者空格分隔
        parts = line.split()

        # 确保这一行至少有2部分（ID 和 分数）
        if len(parts) >= 2:
            sql_id = parts[0]
            score = parts[1]

            if score == '1':
                correct_list.append(sql_id)
            elif score == '0':
                incorrect_list.append(sql_id)

    return correct_list, incorrect_list

# 执行解析
correct, incorrect = parse_data(raw_data)

# 打印结果
print("="*30)
print(f"❌ 错误题目 (共 {len(incorrect)} 题):")
print(incorrect)
print("\n" + "="*30)
print(f"✅ 正确题目 (共 {len(correct)} 题):")
print(correct)
print("="*30)
