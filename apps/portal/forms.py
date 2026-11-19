from django import forms
from django.forms import inlineformset_factory

from apps.organizations.models import Organization
from apps.speakers.models import SpeakerLink, SpeakerProfile

MAX_HEADSHOT_MB = 5


class ProfileForm(forms.ModelForm):
    class Meta:
        model = SpeakerProfile
        fields = [
            "first_name", "last_name", "pronouns", "job_title", "affiliation", "bio", "headshot",
            "email", "phone", "city", "region", "country",
        ]  # fmt: skip
        labels = {
            "affiliation": "Organization / affiliation",
            "email": "Contact email",
            "region": "State / region",
            "country": "Country (2-letter code)",
        }
        help_texts = {
            "bio": "A short third-person bio, as it should appear in the schedule.",
            "email": "Shared only with organizations you allow (see Sharing).",
        }
        widgets = {"bio": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].required = True
        for name, field in self.fields.items():
            if name != "headshot":
                field.widget.attrs.setdefault("class", "input")

    def clean_headshot(self):
        image = self.cleaned_data.get("headshot")
        if image and hasattr(image, "size") and image.size > MAX_HEADSHOT_MB * 1024 * 1024:
            raise forms.ValidationError(f"Please upload an image under {MAX_HEADSHOT_MB} MB.")
        return image

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").upper()


LinkFormSet = inlineformset_factory(
    SpeakerProfile,
    SpeakerLink,
    fields=["kind", "url", "label"],
    extra=2,
    can_delete=True,
    widgets={
        "kind": forms.Select(attrs={"class": "input"}),
        "url": forms.URLInput(attrs={"class": "input", "placeholder": "https://"}),
        "label": forms.TextInput(attrs={"class": "input", "placeholder": "Label (optional)"}),
    },
)


class GrantForm(forms.Form):
    organization = forms.ModelChoiceField(queryset=Organization.objects.none(), empty_label="Choose an organization")

    def __init__(self, *args, exclude_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.exclude(pk__in=exclude_ids).order_by("name")
        self.fields["organization"].widget.attrs["class"] = "input"
