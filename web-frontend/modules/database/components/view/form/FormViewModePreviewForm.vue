<template>
  <div>
    <div
      class="form-view__cover"
      :style="{
        'background-image': view.cover_image
          ? `url(${view.cover_image.url})`
          : null,
      }"
    >
      <template v-if="!readOnly">
        <FormViewImageUpload
          v-if="!view.cover_image"
          @uploaded="updateForm({ cover_image: $event })"
          >{{ $t('formViewModePreviewForm.addCoverImage') }}
        </FormViewImageUpload>
        <a
          v-else
          class="form_view__file-delete"
          @click="updateForm({ cover_image: null })"
        >
          <i class="fas fa-times"></i>
          {{ $t('common.remove') }}
        </a>
      </template>
    </div>
    <div class="form-view__body">
      <div class="form-view__heading">
        <div v-if="view.logo_image !== null" class="form_view__logo">
          <img
            class="form_view__logo-img"
            :src="view.logo_image.url"
            width="200"
          />
          <a
            v-if="!readOnly"
            class="form_view__file-delete"
            @click="updateForm({ logo_image: null })"
          >
            <i class="fas fa-times"></i>
            {{ $t('common.remove') }}
          </a>
        </div>
        <FormViewImageUpload
          v-else-if="!readOnly"
          class="margin-bottom-3"
          @uploaded="updateForm({ logo_image: $event })"
          >{{ $t('formViewModePreviewForm.addLogo') }}
        </FormViewImageUpload>
        <h1 class="form-view__title">
          <Editable
            ref="title"
            :value="view.title"
            @change="updateForm({ title: $event.value })"
            @editing="editingTitle = $event"
          ></Editable>
          <a
            v-if="!readOnly"
            class="form-view__edit"
            :class="{ 'form-view__edit--hidden': editingTitle }"
            @click="$refs.title.edit()"
          ></a>
        </h1>
        <p class="form-view__description">
          <Editable
            ref="description"
            :value="view.description"
            @change="updateForm({ description: $event.value })"
            @editing="editingDescription = $event"
          ></Editable>
          <a
            v-if="!readOnly"
            class="form-view__edit"
            :class="{ 'form-view__edit--hidden': editingDescription }"
            @click="$refs.description.edit()"
          ></a>
        </p>
        <div v-if="fields.length === 0" class="form-view__no-fields">
          {{ $t('formViewModePreviewForm.noFields') }}
        </div>
      </div>
      <div class="form-view__fields">
        <FormViewField
          v-for="field in fields"
          :key="field.id"
          v-sortable="{
            enabled: !readOnly,
            id: field.id,
            update: order,
            handle: '[data-field-handle]',
          }"
          :table="table"
          :view="view"
          :field="field"
          :field-options="fieldOptions[field.id]"
          :fields="fields"
          :read-only="readOnly"
          @hide="updateFieldOptionsOfField(view, field, { enabled: false })"
          @updated-field-options="
            updateFieldOptionsOfField(view, field, $event)
          "
        >
        </FormViewField>
      </div>
      <div class="form-view__actions">
        <FormViewPoweredBy></FormViewPoweredBy>
        <div class="form-view__submit">
          <Editable
            ref="submit_text"
            class="button button--primary button--large"
            :class="{ 'form-view__submit-button-editing': editingSubmitText }"
            :value="view.submit_text"
            @change="updateForm({ submit_text: $event.value })"
            @editing="editingSubmitText = $event"
          ></Editable>
          <a
            v-if="!readOnly"
            class="form-view__edit"
            :class="{ 'form-view__edit--hidden': editingSubmitText }"
            @click="$refs.submit_text.edit()"
          ></a>
        </div>
      </div>
    </div>
    <div class="form-view__meta">
      <div class="form-view__body">
        <FormViewMetaControls
          :view="view"
          :read-only="readOnly"
          @updated-form="updateForm($event)"
        ></FormViewMetaControls>
      </div>
    </div>
  </div>
</template>

<script>
import FormViewField from '@baserow/modules/database/components/view/form/FormViewField'
import FormViewImageUpload from '@baserow/modules/database/components/view/form/FormViewImageUpload'
import formViewHelpers from '@baserow/modules/database/mixins/formViewHelpers'
import FormViewPoweredBy from '@baserow/modules/database/components/view/form/FormViewPoweredBy'
import FormViewMetaControls from '@baserow/modules/database/components/view/form/FormViewMetaControls'

export default {
  name: 'FormViewModePreviewForm',
  components: {
    FormViewPoweredBy,
    FormViewField,
    FormViewImageUpload,
    FormViewMetaControls,
  },
  mixins: [formViewHelpers],
  props: {
    database: {
      type: Object,
      required: true,
    },
    table: {
      type: Object,
      required: true,
    },
    view: {
      type: Object,
      required: true,
    },
    fields: {
      type: Array,
      required: true,
    },
    readOnly: {
      type: Boolean,
      required: true,
    },
  },
  data() {
    return {
      editingTitle: false,
      editingSubmitText: false,
      editingDescription: false,
    }
  },
  methods: {
    order(order) {
      this.$emit('ordered-fields', order)
    },
  },
}
</script>
