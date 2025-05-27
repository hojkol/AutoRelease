import requests
import json
import csv
from datetime import datetime, timezone
import pandas as pd
from collections import OrderedDict
import yaml
import re



ITEM_HEADER = [
    '功能模块',
    '发布版本',
    '发版时间',
    '更新类型',
    '一级功能',
    '二级功能',
    '基线参数'
]


# 1. 获取 tenant_access_token
def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    payload = {"app_id": app_id, "app_secret": app_secret}
    resp = requests.post(url, json=payload)
    return resp.json()["tenant_access_token"]

# 2. 通过 node_token 获取该知识空间节点信息，有 apace_id, 挂载的云资源的 obj_token 和 obj_type
def get_node_info(node_token, tenant_access_token):
    url = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    params = {"token": node_token, "obj_type": "wiki"}
    resp = requests.get(url, params=params, headers=headers)
    return resp.json()


# 3. 读取表格内容
def get_sheet_content(app_token, table_id, view_id, tenant_access_token, page_size=100, page_token=None):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "view_id": view_id,
        # 可根据需要添加筛选条件 filter、排序等
    }
    resp = requests.post(url, headers=headers, data=json.dumps(payload))
    return resp.json()


def get_items(data):
    items = data['data']['items']
    
    filter_items = []
    # CSV表头

    filter_items.append(ITEM_HEADER)
    for item in items:
        fields = item['fields']

        # 处理多文本字段，提取"text"并用逗号连接
        first_level = ','.join([x['text'] for x in fields.get('一级功能', [])]) if fields.get('一级功能') else ''
        second_level = ','.join([x['text'] for x in fields.get('二级功能', [])]) if fields.get('二级功能') else ''
        baseline = ','.join([x['text'] for x in fields.get('基线参数', [])]) if fields.get('基线参数') else ''
        # product_version = ','.join([x['text'] for x in fields.get('产品模块版本', [])]) if fields.get('发布版本') else ''
        version = fields.get('版本', {})['value'][0]['text'] if fields.get('版本') else ''
        release_time = fields.get('发版时间', {})
        if release_time and 'value' in release_time and release_time['value']:
            # 时间戳单位为毫秒，转换为秒后转日期
            timestamp = int(release_time['value'][0]) / 1000
            normal_date = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')
        else:
            normal_date = None
        row = [
            fields.get('功能模块', ''),
            version,
            normal_date,
            fields.get('更新类型', ''),
            first_level,
            second_level,
            baseline
        ]
        # 只保留 产品模块版本 不为空的行
        if version:
            filter_items.append(row)

    # with open('output1.csv', mode='w', newline='', encoding='utf-8') as f:
    #     writer = csv.writer(f)
    #     for i in filter_items:
    #         writer.writerow(i)


    return filter_items


def get_release_Dict(data):
    # 转成DataFrame
    df = pd.DataFrame(data[1:], columns=data[0])

    def version_key(v):
        parts = v.lstrip('v').split('.')
        return tuple(int(x) for x in parts)

    df['版本排序'] = df['发布版本'].apply(version_key)

    # 自定义排序映射
    module_order = {'算力云': 0, '大模型服务平台': 1, '费用中心': 2, '用户中心': 3}
    update_type_order = {'新功能': 0, '增强优化': 1, '故障修复': 2, '无更新类型': 3}

    # 替换空更新类型为'无更新类型'
    df['更新类型'] = df['更新类型'].replace({'': '无更新类型'})

    # 添加排序辅助列
    df['功能模块排序'] = df['功能模块'].map(module_order).fillna(99)
    df['更新类型排序'] = df['更新类型'].map(update_type_order).fillna(99)

    # 按要求排序：
    # 1. 发版时间降序
    # 2. 功能模块自定义顺序升序
    # 3. 版本号升序
    # 4. 更新类型自定义顺序升序
    # 5. 一级功能升序
    df_sorted = df.sort_values(by=['发版时间', '功能模块排序', '版本排序', '更新类型排序', '一级功能'],
                            ascending=[False, True, True, True, True])

    # 构建有序字典结构
    result = OrderedDict()

    for _, row in df_sorted.iterrows():
        pub_date = row['发版时间']
        module = row['功能模块']
        version = row['发布版本']
        update_type = row['更新类型']
        primary_func = row['一级功能']
        entry = {
            '二级功能': row['二级功能'],
            '基线参数': row['基线参数']
        }
        
        if pub_date not in result:
            result[pub_date] = OrderedDict()
        if module not in result[pub_date]:
            result[pub_date][module] = OrderedDict()
        if version not in result[pub_date][module]:
            result[pub_date][module][version] = OrderedDict()
        if update_type not in result[pub_date][module][version]:
            result[pub_date][module][version][update_type] = []
        
        result[pub_date][module][version][update_type].append((primary_func, entry))

    # 组内一级功能再次排序
    for pub_date in result:
        for module in result[pub_date]:
            for version in result[pub_date][module]:
                for update_type in result[pub_date][module][version]:
                    result[pub_date][module][version][update_type].sort(key=lambda x: x[0])
    # # 打印结果示例
    # import pprint
    # pp = pprint.PrettyPrinter(indent=2, width=120)
    # pp.pprint(result)
    return result


def get_release_info(data, filename='rel-notes.md'):
    # result[发布日期][功能模块][版本号][更新类型] = [(一级功能, {二级功能, 基线参数}), ...]
    items = get_items(data)
    result = get_release_Dict(items)

    # 更新类型对应的 Emoji 和标题
    emoji_map = {
        '新功能': '🚀 新功能',
        '增强优化': '⚡ 增强优化',
        '故障修复': '🐛 故障修复',
        '无更新类型': ''  # 空更新类型不显示标题和 Emoji
    }

    # 生成 Markdown 内容
    lines = []
    lines.append('---')
    lines.append('hide:')
    lines.append('  - toc')
    lines.append('---\n')
    lines.append('# Release Notes\n')
    lines.append('本页列出 d.run 各项功能的一些重要变更。\n')

    # 遍历日期（已排序）
    for pub_date, modules in result.items():
        # lines.append(f'## {format_date(pub_date)}\n')
        lines.append(f'## {pub_date}\n')
        # 遍历功能模块
        for module, versions in modules.items():
            # 遍历版本号
            for version, update_types in versions.items():
                lines.append(f'### {module} {version}\n')

                # 按固定顺序输出更新类型
                for update_type in ['新功能', '增强优化', '故障修复', '无更新类型']:
                    if update_type in update_types:
                        entries = update_types[update_type]
                        # 非空更新类型输出标题
                        if update_type != '无更新类型':
                            lines.append(f'#### {emoji_map[update_type]}\n')
                        # 输出每条记录，格式：- [一级功能] 基线参数（若基线参数为空则只显示一级功能）
                        for primary_func, entry in entries:
                            baseline = entry['基线参数']
                            if baseline:
                                lines.append(f'- [{primary_func}] {baseline}')
                            else:
                                lines.append(f'- [{primary_func}]')
                        lines.append('')  # 每个更新类型后空行

    md_content = '\n'.join(lines)

    # 保存为文件
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(md_content)



def parse_feishu_url(url):
    """
    Extract NODE_TOKEN, TABLE_ID, VIEW_ID from Feishu Bitable URL.
    """
    node_token = None
    table_id = None
    view_id = None

    # Extract the node token from the path (after /wiki/)
    match_node = re.search(r'/wiki/([^/?]+)', url)
    if match_node:
        node_token = match_node.group(1)

    # Extract query parameters table and view
    match_table = re.search(r'table=([^&]+)', url)
    if match_table:
        table_id = match_table.group(1)

    match_view = re.search(r'view=([^&]+)', url)
    if match_view:
        view_id = match_view.group(1)

    return node_token, table_id, view_id


def read_config_from_yaml(yaml_path):
    """
    Read config from YAML file, parse URL to get NODE_TOKEN, TABLE_ID, VIEW_ID,
    and return a config dictionary.
    """
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    app_id = data.get('APP_ID')
    app_secret = data.get('APP_SECRET')
    url = data.get('URL')

    node_token, table_id, view_id = parse_feishu_url(url) if url else (None, None, None)

    return app_id, app_secret, node_token, table_id, view_id



if __name__ == "__main__":

    app_path = 'APP.yaml'

    app_id, app_secret, node_token, table_id, view_id = read_config_from_yaml(app_path)

    tenant_access_token = get_tenant_access_token(app_id, app_secret)
    node_info = get_node_info(node_token, tenant_access_token)
    app_token = node_info["data"]["node"]["obj_token"]
    ori_data = get_sheet_content(app_token, table_id, view_id, tenant_access_token)
    # print(ori_data)
    get_release_info(ori_data)

