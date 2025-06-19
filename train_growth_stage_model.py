import os
import json
import torch
from torch import nn, optim
from torchvision import datasets, transforms, models
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
import matplotlib.pyplot as plt
import seaborn as sns
from tqdm import tqdm

# === CONFIGURATION ===
DATA_DIR = r"C:\Users\mbaba\hydrocon\batavia_dataset_labeled"
OUTPUT_DIR = r"C:\Users\mbaba\hydrocon\models"
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODEL_PATH = os.path.join(OUTPUT_DIR, "growth_stage_full_model.pt")
CLASS_MAP_PATH = os.path.join(OUTPUT_DIR, "class_names.json")
EPOCHS = 4
BATCH_SIZE = 32
IMAGE_SIZE = 128
LEARNING_RATE = 0.0005

# === IMAGE TRANSFORM ===
transform = transforms.Compose([
    transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406],
                         [0.229, 0.224, 0.225])
])

# === LOAD DATASET ===
dataset = datasets.ImageFolder(DATA_DIR, transform=transform)
class_names = dataset.classes
with open(CLASS_MAP_PATH, 'w') as f:
    json.dump(class_names, f)
print(f"[INFO] Saved class mapping: {class_names}")

# === SPLIT DATA ===
train_size = int(0.8 * len(dataset))
val_size = len(dataset) - train_size
train_ds, val_ds = torch.utils.data.random_split(dataset, [train_size, val_size])
train_loader = torch.utils.data.DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
val_loader = torch.utils.data.DataLoader(val_ds, batch_size=BATCH_SIZE)

# === DEVICE SETUP ===
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[INFO] Using device: {device}")

# === MODEL SETUP ===
weights = models.ResNet18_Weights.DEFAULT
model = models.resnet18(weights=weights)
model.fc = nn.Linear(model.fc.in_features, len(class_names))
model.to(device)

# === TRAINING SETUP ===
criterion = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
loss_list = []

# === TRAINING LOOP ===
for epoch in range(EPOCHS):
    model.train()
    running_loss = 0.0
    print(f"\n[Epoch {epoch+1}/{EPOCHS}]")

    for imgs, labels in tqdm(train_loader, desc="Training"):
        imgs, labels = imgs.to(device), labels.to(device)

        optimizer.zero_grad()
        outputs = model(imgs)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()
        running_loss += loss.item()

    epoch_loss = running_loss / len(train_loader)
    loss_list.append(epoch_loss)
    print(f"  ✅ Epoch Loss: {epoch_loss:.4f}")

    # Save checkpoint for this epoch
    torch.save(model, os.path.join(OUTPUT_DIR, f"growth_stage_epoch{epoch+1}.pt"))
    print(f"  💾 Model checkpoint saved for epoch {epoch+1}")

# === FINAL EVALUATION ===
model.eval()
y_true, y_pred = [], []
with torch.no_grad():
    for imgs, labels in val_loader:
        imgs = imgs.to(device)
        outputs = model(imgs)
        preds = outputs.argmax(1).cpu()
        y_pred.extend(preds.numpy())
        y_true.extend(labels.numpy())

# === METRICS ===
print("\n📊 Classification Report:")
print(classification_report(y_true, y_pred, target_names=class_names))
print(f"✅ Accuracy: {accuracy_score(y_true, y_pred):.4f}")
print(f"✅ F1 Score: {f1_score(y_true, y_pred, average='weighted'):.4f}")

# === CONFUSION MATRIX ===
cm = confusion_matrix(y_true, y_pred)
plt.figure(figsize=(6, 5))
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=class_names, yticklabels=class_names)
plt.xlabel("Predicted")
plt.ylabel("Actual")
plt.title("Confusion Matrix")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrix.png"))
plt.show()

# === FINAL MODEL SAVE ===
torch.save(model, MODEL_PATH)
print(f"\n💾 Final model saved to: {MODEL_PATH}")

# === LOSS CURVE ===
plt.plot(range(1, EPOCHS + 1), loss_list, marker='o')
plt.title("Training Loss")
plt.xlabel("Epoch")
plt.ylabel("Loss")
plt.grid(True)
plt.savefig(os.path.join(OUTPUT_DIR, "training_loss.png"))
plt.show()
