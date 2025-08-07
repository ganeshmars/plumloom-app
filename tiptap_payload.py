#!/usr/bin/env python3
import json

# The webhook data from your request
webhook_data = {'tiptapJson': {'default': {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': {'id': '5ebf2e2c-6070-4682-8045-39b5a68faa6f'}, 'content': [{'type': 'text', 'text': 'iu rj i fn fgn iufdg, mfdig dfjgi rekjfg iergknd fgidgf'}]}, {'type': 'paragraph', 'attrs': {'id': '604f69b2-693c-471a-af42-f4aa6c9553ba'}, 'content': [{'type': 'text', 'text': 'fldk lk nrei kfgu kerfrng iuerg ifgk fi jg gkjer '}]}, {'type': 'paragraph', 'attrs': {'id': '8e2784b1-017d-4840-ac17-73e31d89c518'}, 'content': [{'type': 'text', 'text': 'e gier erlkng oiergl oergk erufg erg erg ekrg ierklg iefrg'}]}, {'type': 'paragraph', 'attrs': {'id': '22cd38f3-1917-4051-bf7b-d71974c90cf4'}, 'content': [{'type': 'text', 'text': 'e jergml emrog erog erngiu erngiu egn iuergj iuerg'}]}, {'type': 'paragraph', 'attrs': {'id': '55693491-b7fa-48f5-b2f9-d96a1943844e'}, 'content': [{'type': 'text', 'text': 'e rrg0ejrglm eorg legoi okermgoi eokmgo heorng erg'}]}, {'type': 'paragraph', 'attrs': {'id': '32297978-e5a3-4fbe-a7aa-d69b056579dc'}, 'content': [{'type': 'text', 'text': ' erg0i erkgmoi erogmoe jrgkm oieglke ngiu ekjg beg'}]}, {'type': 'paragraph', 'attrs': {'id': '1ac735eb-0cad-4e14-a811-5887cbd78593'}, 'content': [{'type': 'text', 'text': 'e je0rg,ermglk jhoergl wegoi herg erghe'}]}, {'type': 'paragraph', 'attrs': {'id': 'a2720dec-5ce5-4f90-9c7c-babee7162d84'}, 'content': [{'type': 'text', 'text': 'erp gke0rgjle ,eprojg rtegm owe 9pwehrg merogh  ref hgwerg'}]}, {'type': 'paragraph', 'attrs': {'id': 'b2824140-6a08-4840-884b-3c7c78b74922'}, 'content': [{'type': 'text', 'text': 'er je0irgj lerogj okermgi oerm ojerokj emrgoi jekrg her'}]}, {'type': 'paragraph', 'attrs': {'id': '5244d4d4-0d2f-4e1f-bb15-2860eb9b8970'}, 'content': [{'type': 'text', 'text': ' emr0 kermge rlgoi erlg ierwgmo erglk enrgo re'}]}, {'type': 'paragraph', 'attrs': {'id': '76d7c8a6-cab7-46a3-bb8f-6f359377b4e9'}, 'content': [{'type': 'text', 'text': ' lkermg0i leroi elrg oeorm oergom eorg enrgo elkrh og nehr g'}]}, {'type': 'paragraph', 'attrs': {'id': 'da14e8df-22c9-428a-a202-9f1139cd26dc'}, 'content': [{'type': 'text', 'text': 'er jermtlerg9o ertg o9er erogn lekrg oi mero lekrgi lkerjgo iijerg'}]}, {'type': 'paragraph', 'attrs': {'id': '26e9d281-614f-4f55-be6d-5675e6ff7f51'}, 'content': [{'type': 'text', 'text': 'jn hrjni rt erng eknrgi erkgn iegk dgjnik dsbg ig ekgrie ergi be giebrg ierbg egrui bejgbi'}]}, {'type': 'paragraph', 'attrs': {'id': 'eb99f1c5-48c1-4383-b6ab-6e17087a336d'}, 'content': [{'type': 'text', 'text': 'ldk irn hrth ortk roh rhot rorn rntnrhn'}]}, {'type': 'paragraph', 'attrs': {'id': 'f529114d-ce81-470a-b844-7f8cf413ead5'}, 'content': [{'type': 'text', 'text': 'ldn n ur nerng kerg elkrgn ergk nekrg kejnrgkj neiurg egiub kfdjgskjd b'}]}]}}, 'data': '<paragraph id="5ebf2e2c-6070-4682-8045-39b5a68faa6f">iu rj i fn fgn iufdg, mfdig dfjgi rekjfg iergknd fgidgf</paragraph><paragraph id="604f69b2-693c-471a-af42-f4aa6c9553ba">fldk lk nrei kfgu kerfrng iuerg ifgk fi jg gkjer </paragraph><paragraph id="8e2784b1-017d-4840-ac17-73e31d89c518">e gier erlkng oiergl oergk erufg erg erg ekrg ierklg iefrg</paragraph><paragraph id="22cd38f3-1917-4051-bf7b-d71974c90cf4">e jergml emrog erog erngiu erngiu egn iuergj iuerg</paragraph><paragraph id="55693491-b7fa-48f5-b2f9-d96a1943844e">e rrg0ejrglm eorg legoi okermgoi eokmgo heorng erg</paragraph><paragraph id="32297978-e5a3-4fbe-a7aa-d69b056579dc"> erg0i erkgmoi erogmoe jrgkm oieglke ngiu ekjg beg</paragraph><paragraph id="1ac735eb-0cad-4e14-a811-5887cbd78593">e je0rg,ermglk jhoergl wegoi herg erghe</paragraph><paragraph id="a2720dec-5ce5-4f90-9c7c-babee7162d84">erp gke0rgjle ,eprojg rtegm owe 9pwehrg merogh  ref hgwerg</paragraph><paragraph id="b2824140-6a08-4840-884b-3c7c78b74922">er je0irgj lerogj okermgi oerm ojerokj emrgoi jekrg her</paragraph><paragraph id="5244d4d4-0d2f-4e1f-bb15-2860eb9b8970"> emr0 kermge rlgoi erlg ierwgmo erglk enrgo re</paragraph><paragraph id="76d7c8a6-cab7-46a3-bb8f-6f359377b4e9"> lkermg0i leroi elrg oeorm oergom eorg enrgo elkrh og nehr g</paragraph><paragraph id="da14e8df-22c9-428a-a202-9f1139cd26dc">er jermtlerg9o ertg o9er erogn lekrg oi mero lekrgi lkerjgo iijerg</paragraph><paragraph id="26e9d281-614f-4f55-be6d-5675e6ff7f51">jn hrjni rt erng eknrgi erkgn iegk dgjnik dsbg ig ekgrie ergi be giebrg ierbg egrui bejgbi</paragraph><paragraph id="eb99f1c5-48c1-4383-b6ab-6e17087a336d">ldk irn hrth ortk roh rhot rorn rntnrhn</paragraph><paragraph id="f529114d-ce81-470a-b844-7f8cf413ead5">ldn n ur nerng kerg elkrgn ergk nekrg kejnrgkj neiurg egiub kfdjgskjd b</paragraph>', 'clientsCount': 1, 'trigger': 'document.saved', 'users': [], 'appName': '', 'name': 'document_9a7b1561-e6e8-42e6-8afb-aa47a9bef3de', 'time': '2025-05-29T14:12:20.751Z', 'type': 'DOCUMENT'}


# For testing purposes, let's modify the clientsCount to 0 to ensure it triggers processing
# Based on the tiptap_webhook function logic which checks if clientsCount != 0
# webhook_data['clientsCount'] = 0

# Convert to JSON string with proper formatting
json_payload = json.dumps(webhook_data, indent=2)

# Save to a file for easy copying
# with open('tiptap_payload.json', 'w') as f:
#     f.write(json_payload)

# Print the JSON payload
print("JSON Payload for Postman:")
print(json_payload)
print("\nThe payload has also been saved to tiptap_payload.json")

# Print instructions for Postman
print("\nInstructions for Postman:")
print("1. Create a new POST request to your API endpoint: /tiptap/webhook")
print("2. In the Headers tab, add Content-Type: application/json")
print("3. In the Body tab, select 'raw' and 'JSON' format")
print("4. Paste the above JSON payload or load it from the tiptap_payload.json file")
print("5. Send the request")
